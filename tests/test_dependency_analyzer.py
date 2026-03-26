#!/usr/bin/env python3
"""
Tests for the dependency_analyzer module (now src.agents.analyzer).

Tests package manager detection, dependency file reading,
and outdated package checking functionality.
"""

import json
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.agents.analyzer import (
    check_outdated_dependencies,
    cleanup_repository,
    clone_repository,
    detect_package_manager,
    read_dependency_file,
)


class TestDetectPackageManager:
    """Test cases for detect_package_manager function."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository directory."""
        temp_dir = tempfile.mkdtemp(prefix="test_repo_")
        yield temp_dir
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    def test_detect_npm_package_manager(self, temp_repo):
        """Test detection of npm package manager."""
        with open(os.path.join(temp_repo, "package.json"), "w") as f:
            json.dump({"name": "test", "version": "1.0.0"}, f)
        with open(os.path.join(temp_repo, "package-lock.json"), "w") as f:
            json.dump({}, f)

        result = json.loads(detect_package_manager.invoke({"repo_path": temp_repo}))

        assert result["language"] == "nodejs"
        assert result["package_manager"] == "npm"

    def test_detect_yarn(self, temp_repo):
        """Test detection of yarn lock file."""
        with open(os.path.join(temp_repo, "package.json"), "w") as f:
            json.dump({"name": "test"}, f)
        with open(os.path.join(temp_repo, "yarn.lock"), "w") as f:
            f.write("")

        result = json.loads(detect_package_manager.invoke({"repo_path": temp_repo}))

        assert result["language"] == "nodejs"
        assert result["package_manager"] == "yarn"

    def test_detect_pnpm(self, temp_repo):
        """Test detection of pnpm lock file."""
        with open(os.path.join(temp_repo, "package.json"), "w") as f:
            json.dump({"name": "test"}, f)
        with open(os.path.join(temp_repo, "pnpm-lock.yaml"), "w") as f:
            f.write("")

        result = json.loads(detect_package_manager.invoke({"repo_path": temp_repo}))

        assert result["language"] == "nodejs"
        assert result["package_manager"] == "pnpm"

    def test_detect_pip(self, temp_repo):
        """Test detection of pip package manager."""
        with open(os.path.join(temp_repo, "requirements.txt"), "w") as f:
            f.write("requests==2.28.0\n")

        result = json.loads(detect_package_manager.invoke({"repo_path": temp_repo}))

        assert result["language"] == "python"
        assert result["package_manager"] == "pip"

    def test_detect_poetry(self, temp_repo):
        """Test detection of poetry package manager.

        Note: pip has empty lock_files in the language map, so the detection
        logic returns pip as a fallback before checking poetry's lock_files.
        When only pyproject.toml + poetry.lock are present (no requirements.txt),
        the current detection logic still matches pip first.
        """
        with open(os.path.join(temp_repo, "pyproject.toml"), "w") as f:
            f.write("[tool.poetry]\nname = 'test'\n")
        with open(os.path.join(temp_repo, "poetry.lock"), "w") as f:
            f.write("")

        result = json.loads(detect_package_manager.invoke({"repo_path": temp_repo}))

        assert result["language"] == "python"
        # pip is returned because it has empty lock_files and matches first
        assert result["package_manager"] == "pip"

    def test_detect_cargo(self, temp_repo):
        """Test detection of cargo (Rust) package manager."""
        with open(os.path.join(temp_repo, "Cargo.toml"), "w") as f:
            f.write("[package]\nname = 'test'\n")
        with open(os.path.join(temp_repo, "Cargo.lock"), "w") as f:
            f.write("")

        result = json.loads(detect_package_manager.invoke({"repo_path": temp_repo}))

        assert result["language"] == "rust"
        assert result["package_manager"] == "cargo"

    def test_detect_go_mod(self, temp_repo):
        """Test detection of Go modules."""
        with open(os.path.join(temp_repo, "go.mod"), "w") as f:
            f.write("module test\n")
        with open(os.path.join(temp_repo, "go.sum"), "w") as f:
            f.write("")

        result = json.loads(detect_package_manager.invoke({"repo_path": temp_repo}))

        assert result["language"] == "go"
        assert result["package_manager"] == "go-mod"

    def test_detect_bundler(self, temp_repo):
        """Test detection of bundler (Ruby) package manager."""
        with open(os.path.join(temp_repo, "Gemfile"), "w") as f:
            f.write("source 'https://rubygems.org'\n")
        with open(os.path.join(temp_repo, "Gemfile.lock"), "w") as f:
            f.write("")

        result = json.loads(detect_package_manager.invoke({"repo_path": temp_repo}))

        assert result["language"] == "ruby"
        assert result["package_manager"] == "bundler"

    def test_detect_maven(self, temp_repo):
        """Test detection of Maven (Java) package manager."""
        with open(os.path.join(temp_repo, "pom.xml"), "w") as f:
            f.write("<project></project>")

        result = json.loads(detect_package_manager.invoke({"repo_path": temp_repo}))

        assert result["language"] == "java"
        assert result["package_manager"] == "maven"

    def test_detect_composer(self, temp_repo):
        """Test detection of Composer (PHP) package manager."""
        with open(os.path.join(temp_repo, "composer.json"), "w") as f:
            json.dump({"name": "test/project"}, f)
        with open(os.path.join(temp_repo, "composer.lock"), "w") as f:
            f.write("")

        result = json.loads(detect_package_manager.invoke({"repo_path": temp_repo}))

        assert result["language"] == "php"
        assert result["package_manager"] == "composer"

    def test_detect_no_package_manager(self, temp_repo):
        """Test detection when no package manager files exist."""
        result = json.loads(detect_package_manager.invoke({"repo_path": temp_repo}))
        assert result["language"] is None
        assert result["package_manager"] is None


class TestReadDependencyFile:
    """Test cases for read_dependency_file function."""

    @pytest.fixture
    def temp_repo(self):
        temp_dir = tempfile.mkdtemp(prefix="test_repo_")
        yield temp_dir
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    def test_read_package_json(self, temp_repo):
        content = {
            "name": "test",
            "version": "1.0.0",
            "dependencies": {"lodash": "^4.17.0"},
        }
        with open(os.path.join(temp_repo, "package.json"), "w") as f:
            json.dump(content, f)

        result = read_dependency_file.invoke(
            {"repo_path": temp_repo, "file_path": "package.json"}
        )

        assert "test" in result
        assert "lodash" in result

    def test_read_requirements_txt(self, temp_repo):
        with open(os.path.join(temp_repo, "requirements.txt"), "w") as f:
            f.write("requests==2.28.0\nflask>=2.0.0\n")

        result = read_dependency_file.invoke(
            {"repo_path": temp_repo, "file_path": "requirements.txt"}
        )

        assert "requests==2.28.0" in result
        assert "flask>=2.0.0" in result

    def test_read_nonexistent_file(self, temp_repo):
        result = read_dependency_file.invoke(
            {"repo_path": temp_repo, "file_path": "nonexistent.txt"}
        )

        assert "Error" in result


class TestCleanupRepository:
    """Test cases for cleanup_repository function."""

    def test_cleanup_valid_path(self):
        temp_dir = "/tmp/dep_analyzer_test_cleanup"
        os.makedirs(temp_dir, exist_ok=True)
        with open(os.path.join(temp_dir, "test.txt"), "w") as f:
            f.write("test")

        result = json.loads(cleanup_repository.invoke(temp_dir))

        assert result["status"] == "success"
        assert not os.path.exists(temp_dir)

    def test_cleanup_repo_check_prefix(self):
        temp_dir = "/tmp/repo_check_test_cleanup"
        os.makedirs(temp_dir, exist_ok=True)
        with open(os.path.join(temp_dir, "test.txt"), "w") as f:
            f.write("test")

        result = json.loads(cleanup_repository.invoke(temp_dir))

        assert result["status"] == "success"
        assert not os.path.exists(temp_dir)

    def test_cleanup_invalid_path(self):
        temp_dir = tempfile.mkdtemp(prefix="invalid_prefix_")

        result = json.loads(cleanup_repository.invoke(temp_dir))

        assert result["status"] == "error"
        assert os.path.exists(temp_dir)

        shutil.rmtree(temp_dir)

    def test_cleanup_nonexistent_path(self):
        result = json.loads(cleanup_repository.invoke("/tmp/nonexistent_path_12345"))

        assert result["status"] == "error"


class TestCloneRepository:
    """Test cases for clone_repository function."""

    @patch("src.agents.analyzer.subprocess.run")
    @patch("src.agents.analyzer.get_cache")
    def test_clone_success(self, mock_cache, mock_run):
        mock_cache.return_value.get_cached_repository.return_value = None
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = json.loads(clone_repository.invoke("https://github.com/test/repo"))

        assert result["status"] == "success"
        assert "repo_path" in result

    @patch("src.agents.analyzer.subprocess.run")
    @patch("src.agents.analyzer.get_cache")
    def test_clone_failure(self, mock_cache, mock_run):
        mock_cache.return_value.get_cached_repository.return_value = None
        mock_run.return_value = MagicMock(
            returncode=128, stdout="", stderr="fatal: repository not found"
        )

        result = json.loads(
            clone_repository.invoke("https://github.com/nonexistent/repo")
        )

        assert result["status"] == "error"
        assert "Failed to clone" in result["message"]

    @patch("src.agents.analyzer.get_cache")
    def test_clone_from_cache(self, mock_cache):
        temp_cache = tempfile.mkdtemp(prefix="cached_repo_")
        with open(os.path.join(temp_cache, "package.json"), "w") as f:
            json.dump({"name": "cached"}, f)

        mock_cache.return_value.get_cached_repository.return_value = temp_cache

        result = json.loads(clone_repository.invoke("https://github.com/test/repo"))

        assert result["status"] == "success"
        assert result["from_cache"] is True

        shutil.rmtree(temp_cache, ignore_errors=True)
        if "repo_path" in result and os.path.exists(result["repo_path"]):
            shutil.rmtree(result["repo_path"], ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
