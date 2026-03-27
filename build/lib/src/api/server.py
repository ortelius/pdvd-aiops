"""
FastAPI web server for dependency update automation.
Exposes REST endpoints to analyze and update repository dependencies.
"""

import asyncio
import json
import os
import subprocess
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.agents.orchestrator import create_main_orchestrator, validate_prerequisites
from src.utils.docker import get_docker_path

# Load environment variables
load_dotenv()


class RepositoryRequest(BaseModel):
    """Request model for repository operations"""

    repository: str = Field(
        ...,
        description="Repository in format 'owner/repo' or full GitHub URL",
        example="facebook/react",
    )
    github_token: Optional[str] = Field(
        None,
        description="GitHub Personal Access Token (optional, uses env var if not provided)",
    )


class JobResponse(BaseModel):
    """Response model for job submissions"""

    job_id: str
    status: str
    message: str
    repository: str


class UsageResponse(BaseModel):
    """Token usage and cost for a job"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    estimated_cost_usd: float = 0.0


class JobStatusResponse(BaseModel):
    """Response model for job status"""

    job_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    usage: Optional[UsageResponse] = None


# In-memory job storage (use Redis/DB for production)
jobs_storage: Dict[str, Dict[str, Any]] = {}


async def start_persistent_mcp_server():
    """
    Start the persistent MCP server.
    This keeps the MCP container running for the lifetime of the API server.
    """
    from src.integrations.mcp_server_manager import get_mcp_status, start_mcp_server

    success = await start_mcp_server()

    if not success:
        status = await get_mcp_status()
        print(f"  Failed to start persistent MCP server: {status.error_message}")

    return success


async def stop_persistent_mcp_server():
    """Stop the persistent MCP server on shutdown."""
    from src.integrations.mcp_server_manager import stop_mcp_server

    print("Stopping persistent MCP server...")
    await stop_mcp_server()
    print("  Persistent MCP server stopped")


async def setup_github_mcp_docker():
    """
    Verify Docker is available and start the persistent MCP server.
    Image pulling is handled by startup.py's pull_mcp_image().
    """
    print("Setting up GitHub MCP Docker image...")
    docker_cmd = get_docker_path()

    try:
        # Check if Docker is available
        result = subprocess.run(
            [docker_cmd, "--version"], capture_output=True, text=True, timeout=10
        )

        if result.returncode != 0:
            raise RuntimeError("Docker is not available")

        print(f"Docker found: {result.stdout.strip()}")

        # Verify the image exists locally
        verify_result = subprocess.run(
            [docker_cmd, "images", "ghcr.io/github/github-mcp-server", "-q"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if not verify_result.stdout.strip():
            raise RuntimeError(
                "GitHub MCP server image not available. Run startup with --skip-checks disabled to pull it."
            )

        print("GitHub MCP server image verified")

        # Start the persistent MCP server (keeps running until shutdown)
        await start_persistent_mcp_server()

        print("GitHub MCP setup complete")

    except subprocess.TimeoutExpired:
        raise RuntimeError("Docker command timed out")
    except Exception as e:
        raise RuntimeError(f"Failed to setup GitHub MCP Docker: {str(e)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Sets up GitHub MCP Docker on startup and cleans up on shutdown.
    """
    # Startup
    print("Starting Dependency Update API Server...")

    try:
        await setup_github_mcp_docker()
        print("Server ready to accept requests")
    except Exception as e:
        print(f"Startup failed: {str(e)}")
        print("Server will start but may not function correctly")

    yield

    # Shutdown
    print("Shutting down server...")
    await stop_persistent_mcp_server()


# Create FastAPI app with lifespan
app = FastAPI(
    title="Dependency Update Automation API",
    description="Automatically analyze and update repository dependencies with intelligent testing and rollback",
    version="1.0.0",
    lifespan=lifespan,
)


async def process_repository_update(
    job_id: str, repository: str, github_token: Optional[str] = None
):
    """
    Background task to process repository updates.

    Args:
        job_id: Unique job identifier
        repository: Repository to process
        github_token: GitHub token for API operations
    """
    try:
        # Update job status
        jobs_storage[job_id]["status"] = "processing"

        # Validate prerequisites
        print(f"[Job {job_id}] Validating prerequisites...")
        is_valid, message = validate_prerequisites()

        if not is_valid:
            jobs_storage[job_id]["status"] = "failed"
            jobs_storage[job_id]["error"] = message
            return

        # Set GitHub token if provided
        if github_token:
            os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = github_token

        # Create orchestrator agent
        print(f"[Job {job_id}] Creating orchestrator agent...")
        agent = create_main_orchestrator()

        # Run the update process with activity logging
        from src.callbacks.agent_activity import AgentActivityHandler

        handler = AgentActivityHandler("orchestrator", job_id=job_id)

        # Set module-level handler so child agents register for cost aggregation
        import src.agents.orchestrator as orch_module

        orch_module._current_orchestrator_handler = handler

        print(f"[Job {job_id}] Processing repository: {repository}")

        # Run agent.invoke() in a thread so it doesn't block the event loop.
        # This is critical: MCP tool calls use run_coroutine_threadsafe() to
        # schedule async MCP operations back on this event loop. If the loop
        # is blocked by agent.invoke(), it deadlocks.
        import asyncio

        from src.agents.updater import set_main_event_loop

        loop = asyncio.get_running_loop()
        set_main_event_loop(loop)
        result = await loop.run_in_executor(
            None,
            lambda: agent.invoke(
                {
                    "messages": [
                        (
                            "user",
                            f"Analyze and update dependencies for repository: {repository}",
                        )
                    ]
                },
                config={"callbacks": [handler]},
            ),
        )

        # Update job with results
        usage_summary = handler.get_usage_summary()
        final_message = result["messages"][-1].content if result.get("messages") else ""

        # Try to parse structured JSON from orchestrator's final message
        parsed_result = {"output": final_message, "repository": repository}
        try:
            result_json = json.loads(final_message)
            parsed_result["status"] = result_json.get("status", "unknown")
            if "url" in result_json:
                parsed_result["url"] = result_json["url"]
            if "message" in result_json:
                parsed_result["message"] = result_json["message"]
            if "details" in result_json:
                parsed_result["details"] = result_json["details"]
        except (json.JSONDecodeError, TypeError):
            parsed_result["status"] = "completed"

        jobs_storage[job_id]["status"] = "completed"
        jobs_storage[job_id]["result"] = parsed_result
        jobs_storage[job_id]["activity_log"] = handler.activity_log
        jobs_storage[job_id]["usage"] = usage_summary

        print(
            f"[Job {job_id}] Completed — Cost: ${usage_summary['estimated_cost_usd']:.4f} "
            f"({usage_summary['total_tokens']:,} tokens, {usage_summary['llm_calls']} LLM calls)"
        )

    except Exception as e:
        print(f"[Job {job_id}] Failed: {str(e)}")
        jobs_storage[job_id]["status"] = "failed"
        jobs_storage[job_id]["error"] = str(e)


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "online",
        "service": "Dependency Update Automation API",
        "version": "1.0.0",
    }


@app.get("/health")
async def health_check():
    """Detailed health check including Docker and MCP server availability"""
    try:
        from src.integrations.mcp_server_manager import MCPServerStatus, get_mcp_status

        # Check Docker
        docker_cmd = get_docker_path()
        docker_check = subprocess.run(
            [docker_cmd, "--version"], capture_output=True, text=True, timeout=5
        )
        docker_available = docker_check.returncode == 0

        # Check GitHub token
        github_token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
        token_configured = github_token is not None

        # Check Anthropic API key
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        api_key_configured = anthropic_key is not None

        # Check MCP server status
        mcp_status = await get_mcp_status()
        mcp_running = mcp_status.status == MCPServerStatus.RUNNING

        all_healthy = all(
            [docker_available, token_configured, api_key_configured, mcp_running]
        )

        return {
            "status": "healthy" if all_healthy else "degraded",
            "checks": {
                "docker": "available" if docker_available else "unavailable",
                "github_token": "configured" if token_configured else "missing",
                "anthropic_api_key": "configured" if api_key_configured else "missing",
                "mcp_server": {
                    "status": mcp_status.status.value,
                    "tools_count": mcp_status.tools_count,
                    "container_id": mcp_status.container_id[:12]
                    if mcp_status.container_id
                    else None,
                    "error": mcp_status.error_message,
                },
            },
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@app.get("/api/mcp/status")
async def mcp_status():
    """
    Get detailed status of the persistent MCP server.
    """
    from src.integrations.mcp_server_manager import get_mcp_status

    status = await get_mcp_status()

    return {
        "status": status.status.value,
        "container_id": status.container_id,
        "tools_count": status.tools_count,
        "error_message": status.error_message,
        "reconnect_attempts": status.reconnect_attempts,
    }


@app.get("/api/mcp/tools")
async def mcp_tools():
    """
    List all available MCP tools.
    """
    from src.integrations.mcp_server_manager import get_mcp_server

    server = await get_mcp_server()

    if not server.is_running:
        raise HTTPException(status_code=503, detail="MCP server is not running")

    return {"tools_count": len(server.available_tools), "tools": server.available_tools}


@app.post("/api/mcp/reconnect")
async def mcp_reconnect():
    """
    Force reconnection to the MCP server.
    """
    from src.integrations.mcp_server_manager import get_mcp_server

    server = await get_mcp_server()
    success = await server.reconnect()

    if success:
        return {
            "status": "success",
            "message": "MCP server reconnected successfully",
            "tools_count": len(server.available_tools),
        }
    else:
        raise HTTPException(
            status_code=503, detail=f"Failed to reconnect: {server.info.error_message}"
        )


@app.post("/api/repositories/update", response_model=JobResponse)
async def update_repository(
    request: RepositoryRequest, background_tasks: BackgroundTasks
):
    """
    Analyze and update dependencies for a repository.

    The process runs in the background and returns a job ID for status tracking.
    """
    # Generate job ID
    import uuid

    job_id = str(uuid.uuid4())

    # Initialize job
    jobs_storage[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "repository": request.repository,
        "result": None,
        "error": None,
    }

    # Add to background tasks
    background_tasks.add_task(
        process_repository_update,
        job_id=job_id,
        repository=request.repository,
        github_token=request.github_token,
    )

    return JobResponse(
        job_id=job_id,
        status="queued",
        message="Repository update job has been queued",
        repository=request.repository,
    )


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """
    Get the status of a repository update job.
    """
    if job_id not in jobs_storage:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs_storage[job_id]

    usage_data = None
    if job.get("usage"):
        u = job["usage"]
        usage_data = UsageResponse(
            input_tokens=u.get("input_tokens", 0),
            output_tokens=u.get("output_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
            llm_calls=u.get("llm_calls", 0),
            estimated_cost_usd=u.get("estimated_cost_usd", 0.0),
        )

    return JobStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        result=job.get("result"),
        error=job.get("error"),
        usage=usage_data,
    )


@app.get("/api/jobs")
async def list_jobs():
    """List all jobs and their current status"""
    return {"total": len(jobs_storage), "jobs": list(jobs_storage.values())}


if __name__ == "__main__":
    import uvicorn

    # Get port from environment or use default
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")

    print(f"Starting server on {host}:{port}")

    uvicorn.run(
        "src.api.server:app", host=host, port=port, reload=True, log_level="info"
    )
