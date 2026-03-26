#!/usr/bin/env python3
"""
Tests for the github_mcp_client module (now src.integrations.github_mcp_client).

Tests container runtime detection, GitHub MCP client initialization,
and synchronous wrapper functions.
"""

import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.github_mcp_client import (
    GitHubMCPClient,
    _get_event_loop,
    create_issue_sync,
    create_pr_sync,
)
from src.utils.docker import detect_container_runtime, find_command_path


class TestFindCommandPath:
    """Test cases for find_command_path function."""

    def test_find_command_in_path(self):
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/docker"

            result = find_command_path("docker")

            assert result == "/usr/bin/docker"

    def test_find_command_in_common_paths(self):
        with (
            patch("shutil.which") as mock_which,
            patch("os.path.isfile") as mock_isfile,
            patch("os.access") as mock_access,
        ):
            mock_which.return_value = None
            mock_isfile.side_effect = lambda p: p == "/opt/homebrew/bin/docker"
            mock_access.return_value = True

            result = find_command_path("docker")

            assert result == "/opt/homebrew/bin/docker"

    def test_command_not_found(self):
        with (
            patch("shutil.which") as mock_which,
            patch("os.path.isfile") as mock_isfile,
        ):
            mock_which.return_value = None
            mock_isfile.return_value = False

            result = find_command_path("nonexistent")

            assert result is None


class TestDetectContainerRuntime:
    """Test cases for detect_container_runtime function."""

    @patch("src.utils.docker.find_command_path")
    @patch("src.utils.docker.subprocess.run")
    def test_detect_docker(self, mock_run, mock_find):
        mock_find.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0)

        result = detect_container_runtime()

        assert result == "/usr/bin/docker"

    @patch("src.utils.docker.find_command_path")
    @patch("src.utils.docker.subprocess.run")
    def test_detect_podman_when_docker_unavailable(self, mock_run, mock_find):
        def find_side_effect(cmd):
            if cmd == "docker":
                return None
            elif cmd == "podman":
                return "/usr/bin/podman"
            return None

        mock_find.side_effect = find_side_effect
        mock_run.return_value = MagicMock(returncode=0)

        result = detect_container_runtime()

        assert result == "/usr/bin/podman"

    @patch("src.utils.docker.find_command_path")
    def test_no_runtime_available(self, mock_find):
        mock_find.return_value = None

        with pytest.raises(RuntimeError) as exc_info:
            detect_container_runtime()

        assert "No container runtime found" in str(exc_info.value)

    @patch("src.utils.docker.find_command_path")
    @patch("src.utils.docker.subprocess.run")
    def test_returns_full_path_for_non_standard_location(self, mock_run, mock_find):
        mock_find.return_value = "/opt/homebrew/bin/docker"
        mock_run.return_value = MagicMock(returncode=0)

        result = detect_container_runtime()

        assert result == "/opt/homebrew/bin/docker"


class TestGitHubMCPClient:
    """Test cases for GitHubMCPClient class."""

    def test_init_without_token_raises_error(self):
        with patch.dict(os.environ, {}, clear=True):
            if "GITHUB_PERSONAL_ACCESS_TOKEN" in os.environ:
                del os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]

            with pytest.raises(ValueError) as exc_info:
                GitHubMCPClient(github_token=None)

            assert "GitHub token not provided" in str(exc_info.value)

    @patch("src.integrations.github_mcp_client.detect_container_runtime")
    def test_init_with_token(self, mock_detect):
        mock_detect.return_value = "/usr/bin/docker"

        client = GitHubMCPClient(github_token="test_token")

        assert client.github_token == "test_token"
        assert client.container_runtime == "/usr/bin/docker"

    @patch("src.integrations.github_mcp_client.detect_container_runtime")
    def test_init_with_custom_runtime(self, mock_detect):
        client = GitHubMCPClient(
            github_token="test_token", container_runtime="/custom/path/docker"
        )

        assert client.container_runtime == "/custom/path/docker"
        mock_detect.assert_not_called()

    @patch("src.integrations.github_mcp_client.detect_container_runtime")
    def test_init_with_toolsets(self, mock_detect):
        mock_detect.return_value = "/usr/bin/docker"

        client = GitHubMCPClient(
            github_token="test_token", toolsets="repos,issues,pull_requests"
        )

        toolsets_idx = client.server_params.args.index(
            "GITHUB_TOOLSETS=repos,issues,pull_requests"
        )
        assert toolsets_idx > 0


class TestCreatePRSync:
    """Test cases for create_pr_sync function."""

    def test_invalid_repo_name_format(self):
        result = create_pr_sync(
            repo_name="invalid-format",
            branch_name="feature",
            title="Test",
            body="Test body",
        )

        assert result["status"] == "error"
        assert "Invalid repo_name format" in result["message"]

    @patch("src.integrations.github_mcp_client.GitHubMCPClient")
    @patch("src.integrations.github_mcp_client._get_event_loop")
    def test_create_pr_success(self, mock_loop, mock_client_class):
        mock_client = AsyncMock()
        mock_client.create_pull_request.return_value = {
            "status": "success",
            "pr_url": "https://github.com/test/repo/pull/1",
        }
        mock_client_class.return_value.__aenter__.return_value = mock_client

        mock_event_loop = MagicMock()
        mock_event_loop.run_until_complete.return_value = {
            "status": "success",
            "pr_url": "https://github.com/test/repo/pull/1",
        }
        mock_loop.return_value = mock_event_loop

        result = create_pr_sync(
            repo_name="test/repo",
            branch_name="feature-branch",
            title="Test PR",
            body="Test body",
        )

        assert result["status"] == "success"

    @patch("src.integrations.github_mcp_client._get_event_loop")
    def test_create_pr_exception(self, mock_loop):
        mock_loop.return_value.run_until_complete.side_effect = Exception(
            "Connection failed"
        )

        result = create_pr_sync(
            repo_name="test/repo", branch_name="feature", title="Test", body="Test body"
        )

        assert result["status"] == "error"
        assert "Connection failed" in result["message"]


class TestCreateIssueSync:
    """Test cases for create_issue_sync function."""

    def test_invalid_repo_name_format(self):
        result = create_issue_sync(
            repo_name="invalid-format", title="Test Issue", body="Test body"
        )

        assert result["status"] == "error"
        assert "Invalid repo_name format" in result["message"]

    @patch("src.integrations.github_mcp_client._get_event_loop")
    def test_create_issue_success(self, mock_loop):
        mock_loop.return_value.run_until_complete.return_value = {
            "status": "success",
            "issue_url": "https://github.com/test/repo/issues/1",
        }

        result = create_issue_sync(
            repo_name="test/repo", title="Test Issue", body="Test body"
        )

        assert result["status"] == "success"

    def test_labels_parsing(self):
        with patch("src.integrations.github_mcp_client._get_event_loop") as mock_loop:
            mock_loop.return_value.run_until_complete.return_value = {
                "status": "success"
            }

            create_issue_sync(
                repo_name="test/repo",
                title="Test",
                body="Body",
                labels="bug,enhancement,dependencies",
            )


class TestEventLoop:
    """Test cases for event loop handling."""

    def test_get_event_loop_creates_new_loop(self):
        loop = _get_event_loop()

        assert loop is not None
        assert not loop.is_closed()


class TestServerParams:
    """Test cases for MCP server parameters."""

    @patch("src.integrations.github_mcp_client.detect_container_runtime")
    def test_server_params_structure(self, mock_detect):
        mock_detect.return_value = "/usr/bin/docker"

        client = GitHubMCPClient(github_token="test_token")

        assert client.server_params.command == "/usr/bin/docker"
        assert "run" in client.server_params.args
        assert "-i" in client.server_params.args
        assert "--rm" in client.server_params.args
        assert "ghcr.io/github/github-mcp-server" in client.server_params.args
        assert "stdio" in client.server_params.args

    @patch("src.integrations.github_mcp_client.detect_container_runtime")
    def test_token_passed_to_container(self, mock_detect):
        mock_detect.return_value = "/usr/bin/docker"

        client = GitHubMCPClient(github_token="my_secret_token")

        token_env = None
        for i, arg in enumerate(client.server_params.args):
            if "GITHUB_PERSONAL_ACCESS_TOKEN=" in arg:
                token_env = arg
                break

        assert token_env is not None
        assert "my_secret_token" in token_env


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
