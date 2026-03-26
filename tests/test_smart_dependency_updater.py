#!/usr/bin/env python3
"""
Tests for the smart_dependency_updater module.

Tests build command detection, build/test execution,
git operations, and GitHub PR/Issue creation.
"""

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.agents.updater import (
    create_github_issue,
    create_github_pr,
    detect_build_command,
    git_operations,
    run_build_test,
    write_dependency_file,
)


class TestDetectBuildCommand:
    """Test cases for detect_build_command function."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository directory."""
        temp_dir = tempfile.mkdtemp(prefix="test_repo_")
        yield temp_dir
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    def test_detect_npm_commands(self, temp_repo):
        """Test detection of npm commands from package.json."""
        package_json = {
            "name": "test",
            "scripts": {"build": "webpack", "test": "jest", "lint": "eslint ."},
        }
        with open(os.path.join(temp_repo, "package.json"), "w") as f:
            json.dump(package_json, f)

        result = json.loads(detect_build_command.invoke(temp_repo))

        assert result["status"] == "success"
        assert result["commands"]["package_manager"] == "npm"
        assert result["commands"]["install"] == "npm install"
        assert result["commands"]["build"] == "npm run build"
        assert result["commands"]["test"] == "npm test"
        assert result["commands"]["lint"] == "npm run lint"

    def test_detect_yarn_from_lock_file(self, temp_repo):
        """Test detection of yarn from yarn.lock."""
        with open(os.path.join(temp_repo, "package.json"), "w") as f:
            json.dump({"name": "test", "scripts": {"build": "build"}}, f)
        with open(os.path.join(temp_repo, "yarn.lock"), "w") as f:
            f.write("")

        result = json.loads(detect_build_command.invoke(temp_repo))

        assert result["status"] == "success"
        assert result["commands"]["package_manager"] == "yarn"
        assert result["commands"]["install"] == "yarn install"

    def test_detect_pnpm_from_lock_file(self, temp_repo):
        """Test detection of pnpm from pnpm-lock.yaml."""
        with open(os.path.join(temp_repo, "package.json"), "w") as f:
            json.dump({"name": "test", "scripts": {"build": "build"}}, f)
        with open(os.path.join(temp_repo, "pnpm-lock.yaml"), "w") as f:
            f.write("")

        result = json.loads(detect_build_command.invoke(temp_repo))

        assert result["status"] == "success"
        assert result["commands"]["package_manager"] == "pnpm"
        assert result["commands"]["install"] == "pnpm install"

    def test_detect_poetry_commands(self, temp_repo):
        """Test detection of poetry commands."""
        with open(os.path.join(temp_repo, "pyproject.toml"), "w") as f:
            f.write("[tool.poetry]\nname = 'test'\n")

        result = json.loads(detect_build_command.invoke(temp_repo))

        assert result["status"] == "success"
        assert result["commands"]["package_manager"] == "poetry"
        assert result["commands"]["install"] == "poetry install"
        assert result["commands"]["test"] == "poetry run pytest"

    def test_detect_pip_commands(self, temp_repo):
        """Test detection of pip commands."""
        with open(os.path.join(temp_repo, "requirements.txt"), "w") as f:
            f.write("requests==2.28.0\n")

        result = json.loads(detect_build_command.invoke(temp_repo))

        assert result["status"] == "success"
        assert result["commands"]["package_manager"] == "pip"
        assert result["commands"]["install"] == "pip install -r requirements.txt"
        assert result["commands"]["test"] == "pytest"

    def test_detect_pip_with_setup_py(self, temp_repo):
        """Test detection of pip with setup.py and requirements.txt."""
        with open(os.path.join(temp_repo, "requirements.txt"), "w") as f:
            f.write("requests==2.28.0\n")
        with open(os.path.join(temp_repo, "setup.py"), "w") as f:
            f.write("from setuptools import setup\nsetup()")

        result = json.loads(detect_build_command.invoke(temp_repo))

        assert result["status"] == "success"
        assert result["commands"]["build"] == "pip install -r requirements.txt"

    def test_detect_cargo_commands(self, temp_repo):
        """Test detection of cargo commands."""
        with open(os.path.join(temp_repo, "Cargo.toml"), "w") as f:
            f.write("[package]\nname = 'test'\n")

        result = json.loads(detect_build_command.invoke(temp_repo))

        assert result["status"] == "success"
        assert result["commands"]["package_manager"] == "cargo"
        assert result["commands"]["build"] == "cargo build"
        assert result["commands"]["test"] == "cargo test"
        assert result["commands"]["lint"] == "cargo clippy"

    def test_detect_go_commands(self, temp_repo):
        """Test detection of go commands."""
        with open(os.path.join(temp_repo, "go.mod"), "w") as f:
            f.write("module test\n")

        result = json.loads(detect_build_command.invoke(temp_repo))

        assert result["status"] == "success"
        assert result["commands"]["package_manager"] == "go"
        assert result["commands"]["build"] == "go build ./..."
        assert result["commands"]["test"] == "go test ./..."

    def test_detect_bundler_commands(self, temp_repo):
        """Test detection of bundler commands."""
        with open(os.path.join(temp_repo, "Gemfile"), "w") as f:
            f.write("source 'https://rubygems.org'\n")

        result = json.loads(detect_build_command.invoke(temp_repo))

        assert result["status"] == "success"
        assert result["commands"]["package_manager"] == "bundler"
        assert result["commands"]["install"] == "bundle install"
        assert result["commands"]["test"] == "bundle exec rspec"

    def test_detect_composer_commands(self, temp_repo):
        """Test detection of composer commands."""
        with open(os.path.join(temp_repo, "composer.json"), "w") as f:
            json.dump({"name": "test/project"}, f)

        result = json.loads(detect_build_command.invoke(temp_repo))

        assert result["status"] == "success"
        assert result["commands"]["package_manager"] == "composer"
        assert result["commands"]["install"] == "composer install"

    def test_detect_no_package_manager(self, temp_repo):
        """Test when no package manager is detected."""
        result = json.loads(detect_build_command.invoke(temp_repo))

        assert result["status"] == "success"
        assert result["commands"]["package_manager"] is None


class TestRunBuildTest:
    """Test cases for run_build_test function."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository directory."""
        temp_dir = tempfile.mkdtemp(prefix="test_repo_")
        yield temp_dir
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    def test_run_successful_command(self, temp_repo):
        """Test running a successful command."""
        original_dir = os.getcwd()

        result = json.loads(
            run_build_test.invoke(
                {"repo_path": temp_repo, "command": "echo 'Hello World'"}
            )
        )

        assert result["status"] == "success"
        assert result["succeeded"] is True
        assert result["exit_code"] == 0
        assert "Hello World" in result["stdout"]
        # Verify working directory restored
        assert os.getcwd() == original_dir

    def test_run_failing_command(self, temp_repo):
        """Test running a failing command."""
        result = json.loads(
            run_build_test.invoke({"repo_path": temp_repo, "command": "exit 1"})
        )

        assert result["status"] == "success"  # Tool succeeded, command failed
        assert result["succeeded"] is False
        assert result["exit_code"] == 1

    def test_run_command_with_output(self, temp_repo):
        """Test command output capture."""
        result = json.loads(
            run_build_test.invoke(
                {
                    "repo_path": temp_repo,
                    "command": "echo 'stdout message' && echo 'stderr message' >&2",
                }
            )
        )

        assert result["status"] == "success"
        assert "stdout message" in result["stdout"]
        assert "stderr message" in result["stderr"]

    def test_run_command_timeout(self, temp_repo):
        """Test command timeout handling."""
        result = json.loads(
            run_build_test.invoke(
                {"repo_path": temp_repo, "command": "sleep 10", "timeout": 1}
            )
        )

        assert result["status"] == "error"
        assert "timed out" in result["message"]

    def test_working_directory_restored_on_timeout(self, temp_repo):
        """Test that working directory is restored after timeout."""
        original_dir = os.getcwd()

        run_build_test.invoke(
            {"repo_path": temp_repo, "command": "sleep 10", "timeout": 1}
        )

        assert os.getcwd() == original_dir

    def test_working_directory_restored_on_error(self, temp_repo):
        """Test that working directory is restored on error."""
        original_dir = os.getcwd()

        # Run a command that fails
        run_build_test.invoke(
            {
                "repo_path": temp_repo,
                "command": "false",  # Always fails
            }
        )

        assert os.getcwd() == original_dir


class TestWriteDependencyFile:
    """Test cases for write_dependency_file function."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository directory."""
        temp_dir = tempfile.mkdtemp(prefix="test_repo_")
        yield temp_dir
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    def test_write_package_json(self, temp_repo):
        """Test writing package.json file."""
        content = json.dumps({"name": "test", "version": "1.0.0"}, indent=2)

        result = json.loads(
            write_dependency_file.invoke(
                {
                    "repo_path": temp_repo,
                    "file_name": "package.json",
                    "content": content,
                }
            )
        )

        assert result["status"] == "success"

        # Verify file was written
        with open(os.path.join(temp_repo, "package.json")) as f:
            written_content = f.read()
        assert "test" in written_content

    def test_write_requirements_txt(self, temp_repo):
        """Test writing requirements.txt file."""
        content = "requests==2.31.0\nflask==3.0.0\n"

        result = json.loads(
            write_dependency_file.invoke(
                {
                    "repo_path": temp_repo,
                    "file_name": "requirements.txt",
                    "content": content,
                }
            )
        )

        assert result["status"] == "success"

        with open(os.path.join(temp_repo, "requirements.txt")) as f:
            written_content = f.read()
        assert "requests==2.31.0" in written_content

    def test_write_overwrites_existing(self, temp_repo):
        """Test that writing overwrites existing file."""
        file_path = os.path.join(temp_repo, "test.txt")
        with open(file_path, "w") as f:
            f.write("old content")

        write_dependency_file.invoke(
            {"repo_path": temp_repo, "file_name": "test.txt", "content": "new content"}
        )

        with open(file_path) as f:
            assert f.read() == "new content"


class TestGitOperations:
    """Test cases for git_operations function."""

    @pytest.fixture
    def git_repo(self):
        """Create a temporary git repository."""
        temp_dir = tempfile.mkdtemp(prefix="test_git_")
        # Use subprocess for better error handling and compatibility
        import subprocess

        subprocess.run(["git", "init"], cwd=temp_dir, capture_output=True)
        subprocess.run(
            ["git", "checkout", "-b", "main"], cwd=temp_dir, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=temp_dir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=temp_dir,
            capture_output=True,
        )
        # Disable GPG signing for test commits
        subprocess.run(
            ["git", "config", "commit.gpgsign", "false"],
            cwd=temp_dir,
            capture_output=True,
        )

        # Create initial commit
        with open(os.path.join(temp_dir, "README.md"), "w") as f:
            f.write("# Test")
        subprocess.run(["git", "add", "."], cwd=temp_dir, capture_output=True)
        subprocess.run(
            ["git", "commit", "--no-gpg-sign", "-m", "Initial commit"],
            cwd=temp_dir,
            capture_output=True,
        )

        yield temp_dir
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    @patch("src.agents.updater._run_mcp_call")
    def test_create_branch(self, mock_run_mcp_call, git_repo):
        """Test creating a new git branch via MCP."""
        # Add a remote so get_remote_url works
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/test/repo.git"],
            cwd=git_repo,
            capture_output=True,
        )
        mock_run_mcp_call.return_value = {"status": "success"}

        result = json.loads(
            git_operations.invoke({"repo_path": git_repo, "operation": "create_branch"})
        )

        assert result["status"] == "success"
        assert result["branch_name"].startswith("OrteliusAiBot/dep-")
        mock_run_mcp_call.assert_called_once()

    @patch("src.agents.updater._run_mcp_call")
    def test_create_branch_default_name(self, mock_run_mcp_call, git_repo):
        """Test creating branch with default name via MCP."""
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/test/repo.git"],
            cwd=git_repo,
            capture_output=True,
        )
        mock_run_mcp_call.return_value = {"status": "success"}

        result = json.loads(
            git_operations.invoke({"repo_path": git_repo, "operation": "create_branch"})
        )

        assert result["status"] == "success"
        assert result["branch_name"].startswith("OrteliusAiBot/dep-")

    @patch("src.agents.updater._run_mcp_call")
    def test_push_files(self, mock_run_mcp_call, git_repo):
        """Test pushing modified files via MCP."""
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/test/repo.git"],
            cwd=git_repo,
            capture_output=True,
        )
        mock_run_mcp_call.return_value = {"status": "success"}

        # Make a change (untracked by git diff, but tracked as modified)
        with open(os.path.join(git_repo, "README.md"), "w") as f:
            f.write("# Updated")

        result = json.loads(
            git_operations.invoke(
                {
                    "repo_path": git_repo,
                    "operation": "push_files",
                    "kwargs": {
                        "branch_name": "update-deps",
                        "message": "chore: update deps",
                    },
                }
            )
        )

        assert result["status"] == "success"
        assert result["files_pushed"] >= 1
        mock_run_mcp_call.assert_called_once()

    @patch("src.agents.updater._run_mcp_call")
    def test_push_files_no_changes(self, mock_run_mcp_call, git_repo):
        """Test push_files when no files are modified."""
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/test/repo.git"],
            cwd=git_repo,
            capture_output=True,
        )

        result = json.loads(
            git_operations.invoke(
                {
                    "repo_path": git_repo,
                    "operation": "push_files",
                    "kwargs": {
                        "branch_name": "update-deps",
                        "message": "chore: update deps",
                    },
                }
            )
        )

        assert result["status"] == "no_changes"
        mock_run_mcp_call.assert_not_called()

    def test_get_remote_url(self, git_repo):
        """Test getting remote URL."""
        # Add a remote
        os.system(
            f"cd {git_repo} && git remote add origin https://github.com/test/repo.git"
        )

        result = json.loads(
            git_operations.invoke(
                {"repo_path": git_repo, "operation": "get_remote_url"}
            )
        )

        assert result["status"] == "success"
        assert "test/repo" in result["repo_name"]

    def test_unknown_operation(self, git_repo):
        """Test handling of unknown operation."""
        result = json.loads(
            git_operations.invoke(
                {"repo_path": git_repo, "operation": "unknown_operation"}
            )
        )

        assert result["status"] == "error"
        assert "Unknown operation" in result["message"]

    def test_working_directory_restored(self, git_repo):
        """Test that working directory is restored after operations."""
        original_dir = os.getcwd()

        git_operations.invoke({"repo_path": git_repo, "operation": "get_remote_url"})

        assert os.getcwd() == original_dir


class TestCreateGitHubPR:
    """Test cases for create_github_pr function."""

    @patch("src.agents.updater._run_mcp_call")
    def test_create_pr_success(self, mock_run_mcp_call):
        """Test successful PR creation via persistent MCP."""
        mock_run_mcp_call.return_value = {
            "status": "success",
            "data": {"html_url": "https://github.com/test/repo/pull/1", "number": 1},
        }

        from src.agents.updater import create_github_pr as create_pr_func

        result = json.loads(
            create_pr_func.invoke(
                {
                    "repo_name": "test/repo",
                    "branch_name": "feature-branch",
                    "title": "Update dependencies",
                    "body": "This PR updates dependencies",
                    "base_branch": "main",
                }
            )
        )

        assert result["status"] == "success"
        assert "pr_url" in result

    @patch("src.agents.updater._run_mcp_call")
    def test_create_pr_failure(self, mock_run_mcp_call):
        """Test PR creation failure."""
        mock_run_mcp_call.return_value = {
            "status": "error",
            "message": "Authentication failed",
        }

        from src.agents.updater import create_github_pr as create_pr_func

        result = json.loads(
            create_pr_func.invoke(
                {
                    "repo_name": "test/repo",
                    "branch_name": "feature-branch",
                    "title": "Update dependencies",
                    "body": "This PR updates dependencies",
                }
            )
        )

        assert result["status"] == "error"


class TestCreateGitHubIssue:
    """Test cases for create_github_issue function."""

    @patch("src.agents.updater._run_mcp_call")
    def test_create_issue_success(self, mock_run_mcp_call):
        """Test successful issue creation via persistent MCP."""
        mock_run_mcp_call.return_value = {
            "status": "success",
            "data": {"html_url": "https://github.com/test/repo/issues/1", "number": 1},
        }

        from src.agents.updater import create_github_issue as create_issue_func

        result = json.loads(
            create_issue_func.invoke(
                {
                    "repo_name": "test/repo",
                    "title": "Dependency update failed",
                    "body": "Could not update dependencies",
                    "labels": "dependencies,bug",
                }
            )
        )

        assert result["status"] == "success"
        assert "issue_url" in result

    @patch("src.agents.updater._run_mcp_call")
    def test_create_issue_failure(self, mock_run_mcp_call):
        """Test issue creation failure."""
        mock_run_mcp_call.return_value = {
            "status": "error",
            "message": "Repository not found",
        }

        from src.agents.updater import create_github_issue as create_issue_func

        result = json.loads(
            create_issue_func.invoke(
                {"repo_name": "test/repo", "title": "Test issue", "body": "Test body"}
            )
        )

        assert result["status"] == "error"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
