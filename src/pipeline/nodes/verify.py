"""
Verification agent node — LLM-powered (Sonnet).

Runs verification checks (Dockerfile, CI config, etc.) using the tool registry.
The agent reasons about findings and produces structured results.
"""

import json
import os
from typing import Optional

from langchain.agents import create_agent
from langchain_core.tools import tool

from src.callbacks.agent_activity import AgentActivityHandler
from src.config.llm import get_llm
from src.pipeline.state import PipelineState
from src.tools.verification_tools import (
    build_verification_prompt_section,
    get_verification_tools,
)


@tool
def read_file(repo_path: str, file_path: str) -> str:
    """
    Read a file from the repository for verification purposes.

    Args:
        repo_path: Path to the repository
        file_path: Relative path to the file within the repo

    Returns:
        File contents (truncated to 3000 chars)
    """
    try:
        full_path = os.path.join(repo_path, file_path)
        # Prevent path traversal
        real_path = os.path.realpath(full_path)
        if not real_path.startswith(os.path.realpath(repo_path)):
            return json.dumps({"status": "error", "message": "Path traversal detected"})

        with open(full_path, "r") as f:
            content = f.read()
        return json.dumps({
            "status": "success",
            "file": file_path,
            "content": content[:3000],
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def verify_node(state: PipelineState) -> dict:
    """
    Run verification checks via LLM agent with registered tools.

    The agent receives the update context and uses verification tools
    to check for consistency issues (Dockerfile, CI configs, etc.).

    Returns: verification_results
    """
    repo_path = state["repo_path"]
    applied_updates = state.get("applied_updates", [])
    package_manager = state.get("package_manager", "")
    tracker = state.get("cost_tracker")

    # Get applicable verification tools
    verification_tools = get_verification_tools(repo_path)

    if not verification_tools:
        # No verification checks apply — skip
        return {"verification_results": []}

    # Check if LLM is available (skip verification if no API credits)
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key or api_key.startswith("test-") or api_key == "your-anthropic-api-key-here":
        print("  [verify] Skipping — no Anthropic API key configured")
        return {"verification_results": [{"check": "skipped", "status": "warn",
                "detail": "Verification skipped — no API credits available"}]}

    if tracker:
        tracker.start_phase("verification_agent")

    try:
        # Build dynamic verification section
        checks_section = build_verification_prompt_section(repo_path)

        # All tools for the agent
        tools = verification_tools + [read_file]

        system_prompt = f"""You are a dependency update verification agent.
Dependency updates have already been applied and tests pass.
Your job: run verification checks to identify potential issues.

## APPLIED UPDATES
{json.dumps(applied_updates, indent=2)}

Package manager: {package_manager}
Repository: {repo_path}

## VERIFICATION CHECKS
{checks_section}

## RULES
- Call each applicable verification tool.
- Analyze the findings — look for version conflicts, outdated base images,
  CI configs that need updating, etc.
- Use read_file if you need to inspect any file for additional context.
- Your final response MUST be ONLY a JSON array of check results:
  [{{"check": "name", "status": "pass|warn|fail", "detail": "explanation"}}]
- Keep responses under 50 words except for the final JSON."""

        llm = get_llm(temperature=0)

        agent = create_agent(llm, tools, system_prompt=system_prompt)
        handler = AgentActivityHandler("verification")

        result = agent.invoke(
            {
                "messages": [(
                    "user",
                    "Run all applicable verification checks and return results as JSON.",
                )]
            },
            config={"callbacks": [handler], "recursion_limit": 20},
        )

        if tracker:
            tracker.merge_agent_handler(handler)

        # Parse verification results
        final_message = result["messages"][-1].content
        try:
            verification_results = json.loads(final_message)
            if not isinstance(verification_results, list):
                verification_results = [verification_results]
        except json.JSONDecodeError:
            verification_results = [{"check": "verification", "status": "warn",
                                      "detail": final_message[:200]}]

        return {"verification_results": verification_results}

    except Exception as e:
        error_msg = str(e)
        if "credit balance" in error_msg or "too low" in error_msg or "401" in error_msg:
            print(f"  [verify] Skipping — API credits exhausted")
            return {"verification_results": [
                {"check": "skipped", "status": "warn", "detail": "Verification skipped — API credits exhausted"}
            ]}
        return {"verification_results": [
            {"check": "verification", "status": "error", "detail": error_msg[:200]}
        ]}
    finally:
        if tracker:
            tracker.end_phase()
