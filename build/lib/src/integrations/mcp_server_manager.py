#!/usr/bin/env python3
"""
Persistent MCP Server Manager

Manages a long-running GitHub MCP server container that stays active
for the lifetime of the API server, providing better performance by
avoiding container startup overhead for each request.
"""

import asyncio
import os
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from src.utils.docker import detect_container_runtime, get_docker_path

load_dotenv()


class MCPServerStatus(Enum):
    """Status of the MCP server."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"
    RECONNECTING = "reconnecting"


@dataclass
class MCPServerInfo:
    """Information about the MCP server state."""

    status: MCPServerStatus
    container_id: Optional[str] = None
    tools_count: int = 0
    error_message: Optional[str] = None
    reconnect_attempts: int = 0


class PersistentMCPServer:
    """
    Manages a persistent GitHub MCP server connection.

    This class maintains a long-running MCP client session that can be
    reused across multiple API requests, improving performance by avoiding
    the overhead of starting a new container for each request.
    """

    _instance: Optional["PersistentMCPServer"] = None
    _lock = asyncio.Lock()

    def __init__(self):
        """Initialize the MCP server manager."""
        self.github_token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
        self.docker_path = get_docker_path()
        self.container_runtime = self._detect_runtime()

        self._client = None
        self._session = None
        self._stdio_context = None
        self._status = MCPServerStatus.STOPPED
        self._container_id: Optional[str] = None
        self._tools: List[str] = []
        self._error_message: Optional[str] = None
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 3

    @classmethod
    async def get_instance(cls) -> "PersistentMCPServer":
        """Get or create the singleton instance."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    async def shutdown_instance(cls):
        """Shutdown and cleanup the singleton instance."""
        if cls._instance is not None:
            await cls._instance.stop()
            cls._instance = None

    def _detect_runtime(self) -> str:
        """Detect available container runtime."""
        try:
            return detect_container_runtime()
        except RuntimeError:
            return self.docker_path

    @property
    def status(self) -> MCPServerStatus:
        """Get current server status."""
        return self._status

    @property
    def info(self) -> MCPServerInfo:
        """Get detailed server information."""
        return MCPServerInfo(
            status=self._status,
            container_id=self._container_id,
            tools_count=len(self._tools),
            error_message=self._error_message,
            reconnect_attempts=self._reconnect_attempts,
        )

    @property
    def available_tools(self) -> List[str]:
        """Get list of available MCP tools."""
        return self._tools.copy()

    @property
    def is_running(self) -> bool:
        """Check if the MCP server is running and healthy."""
        return self._status == MCPServerStatus.RUNNING and self._session is not None

    async def start(self) -> bool:
        """
        Start the persistent MCP server.

        Returns:
            True if started successfully, False otherwise
        """
        if not self.github_token:
            self._status = MCPServerStatus.ERROR
            self._error_message = "GITHUB_PERSONAL_ACCESS_TOKEN not set"
            return False

        self._status = MCPServerStatus.STARTING
        self._error_message = None

        try:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client

            print("Starting persistent MCP server...")

            server_params = StdioServerParameters(
                command=self.container_runtime,
                args=[
                    "run",
                    "-i",
                    "--rm",
                    "-e",
                    f"GITHUB_PERSONAL_ACCESS_TOKEN={self.github_token}",
                    "ghcr.io/github/github-mcp-server",
                    "stdio",
                ],
                env={"GITHUB_PERSONAL_ACCESS_TOKEN": self.github_token},
            )

            self._stdio_context = stdio_client(server_params)
            streams = await self._stdio_context.__aenter__()

            self._session = ClientSession(*streams)
            await self._session.__aenter__()
            await self._session.initialize()

            # Get available tools
            tools_result = await self._session.list_tools()
            self._tools = [tool.name for tool in tools_result.tools]

            # Try to get container ID (for monitoring)
            self._container_id = await self._get_running_container_id()

            self._status = MCPServerStatus.RUNNING
            self._reconnect_attempts = 0

            print(f"Persistent MCP server started - {len(self._tools)} tools available")
            if self._container_id:
                print(f"   Container ID: {self._container_id[:12]}")

            return True

        except Exception as e:
            self._status = MCPServerStatus.ERROR
            self._error_message = str(e)
            print(f"Failed to start MCP server: {e}")
            await self._cleanup()
            return False

    async def stop(self):
        """Stop the persistent MCP server."""
        print("Stopping persistent MCP server...")
        await self._cleanup()
        self._status = MCPServerStatus.STOPPED
        self._container_id = None
        self._tools = []
        print("MCP server stopped")

    async def _cleanup(self):
        """Clean up resources."""
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None

        if self._stdio_context:
            try:
                await self._stdio_context.__aexit__(None, None, None)
            except Exception:
                pass
            self._stdio_context = None

        self._client = None

    async def _get_running_container_id(self) -> Optional[str]:
        """Try to get the ID of the running MCP container."""
        try:
            result = subprocess.run(
                [
                    self.docker_path,
                    "ps",
                    "-q",
                    "--filter",
                    "ancestor=ghcr.io/github/github-mcp-server",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Return the most recent container
                return result.stdout.strip().split("\n")[0]
        except Exception:
            pass
        return None

    async def reconnect(self) -> bool:
        """
        Attempt to reconnect to the MCP server.

        Returns:
            True if reconnected successfully, False otherwise
        """
        if self._reconnect_attempts >= self._max_reconnect_attempts:
            self._status = MCPServerStatus.ERROR
            self._error_message = (
                f"Max reconnect attempts ({self._max_reconnect_attempts}) exceeded"
            )
            return False

        self._status = MCPServerStatus.RECONNECTING
        self._reconnect_attempts += 1

        print(f"Reconnecting to MCP server (attempt {self._reconnect_attempts})...")

        await self._cleanup()

        # Wait a bit before reconnecting
        await asyncio.sleep(1)

        return await self.start()

    async def ensure_connected(self) -> bool:
        """
        Ensure the MCP server is connected, attempting reconnection if needed.

        Returns:
            True if connected, False otherwise
        """
        if self.is_running:
            return True

        if self._status == MCPServerStatus.STOPPED:
            return await self.start()

        if self._status in (MCPServerStatus.ERROR, MCPServerStatus.RECONNECTING):
            return await self.reconnect()

        return False

    async def call_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Call an MCP tool.

        Args:
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            Tool result as a dictionary
        """
        if not await self.ensure_connected():
            return {
                "status": "error",
                "message": f"MCP server not available: {self._error_message}",
            }

        try:
            result = await self._session.call_tool(tool_name, arguments=arguments)

            if result.content and len(result.content) > 0:
                response_text = (
                    result.content[0].text
                    if hasattr(result.content[0], "text")
                    else str(result.content[0])
                )

                import json

                try:
                    return {"status": "success", "data": json.loads(response_text)}
                except json.JSONDecodeError:
                    return {"status": "success", "data": response_text}

            return {"status": "error", "message": "No response from MCP server"}

        except Exception as e:
            # Try to reconnect on error
            self._status = MCPServerStatus.ERROR
            self._error_message = str(e)

            if await self.reconnect():
                # Retry the call after reconnection
                return await self.call_tool(tool_name, arguments)

            return {"status": "error", "message": f"MCP call failed: {str(e)}"}

    async def create_pull_request(
        self,
        repo_owner: str,
        repo_name: str,
        title: str,
        body: str,
        head: str,
        base: str = "main",
    ) -> Dict[str, Any]:
        """Create a GitHub Pull Request using MCP."""
        return await self.call_tool(
            "create_pull_request",
            {
                "owner": repo_owner,
                "repo": repo_name,
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )

    async def create_issue(
        self,
        repo_owner: str,
        repo_name: str,
        title: str,
        body: str,
        labels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a GitHub Issue using MCP."""
        if labels is None:
            labels = ["dependencies"]

        return await self.call_tool(
            "issue_write",
            {
                "method": "create",
                "owner": repo_owner,
                "repo": repo_name,
                "title": title,
                "body": body,
                "labels": labels,
            },
        )

    async def create_branch(
        self,
        repo_owner: str,
        repo_name: str,
        branch: str,
        from_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new branch in a GitHub repository using MCP."""
        args = {
            "owner": repo_owner,
            "repo": repo_name,
            "branch": branch,
        }
        if from_branch:
            args["from_branch"] = from_branch
        return await self.call_tool("create_branch", args)

    async def push_files(
        self,
        repo_owner: str,
        repo_name: str,
        branch: str,
        files: List[Dict[str, str]],
        message: str,
    ) -> Dict[str, Any]:
        """Push multiple files to a GitHub repository in a single commit using MCP."""
        return await self.call_tool(
            "push_files",
            {
                "owner": repo_owner,
                "repo": repo_name,
                "branch": branch,
                "files": files,
                "message": message,
            },
        )


# Convenience functions for getting the global MCP server instance
async def get_mcp_server() -> PersistentMCPServer:
    """Get the global persistent MCP server instance."""
    return await PersistentMCPServer.get_instance()


async def start_mcp_server() -> bool:
    """Start the global persistent MCP server."""
    server = await get_mcp_server()
    return await server.start()


async def stop_mcp_server():
    """Stop the global persistent MCP server."""
    await PersistentMCPServer.shutdown_instance()


async def get_mcp_status() -> MCPServerInfo:
    """Get the status of the global MCP server."""
    server = await get_mcp_server()
    return server.info
