#!/usr/bin/env python3
"""
Tests for the repository_cache module.

Tests caching functionality including:
- Cache key generation
- Repository caching
- Analysis caching
- Outdated packages caching
- Cache expiration
- Cache cleanup
"""

import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.cache import RepositoryCache, get_cache


class TestRepositoryCache:
    """Test cases for RepositoryCache class."""

    @pytest.fixture
    def temp_cache_dir(self):
        """Create a temporary directory for cache testing."""
        temp_dir = tempfile.mkdtemp(prefix="test_cache_")
        yield temp_dir
        # Cleanup
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    @pytest.fixture
    def cache(self, temp_cache_dir):
        """Create a RepositoryCache instance with temp directory."""
        return RepositoryCache(cache_dir=temp_cache_dir, expiry_hours=24)

    @pytest.fixture
    def sample_repo(self):
        """Create a sample repository directory."""
        temp_dir = tempfile.mkdtemp(prefix="test_repo_")
        # Create some files
        with open(os.path.join(temp_dir, "package.json"), "w") as f:
            json.dump({"name": "test-project", "version": "1.0.0"}, f)
        with open(os.path.join(temp_dir, "README.md"), "w") as f:
            f.write("# Test Project")
        yield temp_dir
        # Cleanup
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    # Cache Key Generation Tests
    def test_cache_key_from_github_url(self, cache):
        """Test cache key generation from GitHub URL."""
        url = "https://github.com/owner/repo"
        key = cache._get_repo_cache_key(url)
        assert key == "owner_repo"

    def test_cache_key_from_github_url_with_git_suffix(self, cache):
        """Test cache key generation from GitHub URL with .git suffix."""
        url = "https://github.com/owner/repo.git"
        key = cache._get_repo_cache_key(url)
        assert key == "owner_repo"

    def test_cache_key_from_owner_repo_format(self, cache):
        """Test cache key generation from owner/repo format."""
        repo = "owner/repo"
        key = cache._get_repo_cache_key(repo)
        assert key == "owner_repo"

    def test_cache_key_from_github_url_with_trailing_slash(self, cache):
        """Test cache key generation from URL with trailing slash."""
        url = "https://github.com/owner/repo/"
        key = cache._get_repo_cache_key(url)
        assert key == "owner_repo"

    # Repository Caching Tests
    def test_cache_repository(self, cache, sample_repo):
        """Test caching a repository."""
        repo_url = "https://github.com/test/project"
        cache.cache_repository(repo_url, sample_repo)

        # Verify cache was created
        cached_path = cache.get_cached_repository(repo_url)
        assert cached_path is not None
        assert os.path.exists(cached_path)
        assert os.path.exists(os.path.join(cached_path, "package.json"))

    def test_get_cached_repository_not_found(self, cache):
        """Test getting a non-existent cached repository."""
        cached_path = cache.get_cached_repository("https://github.com/nonexistent/repo")
        assert cached_path is None

    def test_cache_repository_overwrites_existing(self, cache, sample_repo):
        """Test that caching a repository overwrites existing cache."""
        repo_url = "https://github.com/test/project"

        # Cache once
        cache.cache_repository(repo_url, sample_repo)

        # Modify sample repo
        with open(os.path.join(sample_repo, "new_file.txt"), "w") as f:
            f.write("new content")

        # Cache again
        cache.cache_repository(repo_url, sample_repo)

        # Verify new file exists in cache
        cached_path = cache.get_cached_repository(repo_url)
        assert os.path.exists(os.path.join(cached_path, "new_file.txt"))

    # Analysis Caching Tests
    def test_cache_analysis(self, cache):
        """Test caching analysis results."""
        repo_url = "https://github.com/test/project"
        analysis_data = {
            "package_manager": "npm",
            "dependencies": ["react", "lodash"],
            "outdated_count": 2,
        }

        cache.cache_analysis(repo_url, analysis_data)

        # Retrieve cached analysis
        cached = cache.get_cached_analysis(repo_url)
        assert cached is not None
        assert cached["package_manager"] == "npm"
        assert cached["outdated_count"] == 2

    def test_get_cached_analysis_not_found(self, cache):
        """Test getting non-existent analysis cache."""
        cached = cache.get_cached_analysis("https://github.com/nonexistent/repo")
        assert cached is None

    # Outdated Packages Caching Tests
    def test_cache_outdated(self, cache):
        """Test caching outdated packages information."""
        repo_url = "https://github.com/test/project"
        outdated_data = {
            "package_manager": "npm",
            "outdated_packages": [
                {"name": "react", "current": "17.0.0", "latest": "18.0.0"}
            ],
        }

        cache.cache_outdated(repo_url, outdated_data)

        # Retrieve cached outdated data
        cached = cache.get_cached_outdated(repo_url)
        assert cached is not None
        assert cached["package_manager"] == "npm"
        assert len(cached["outdated_packages"]) == 1

    def test_get_cached_outdated_not_found(self, cache):
        """Test getting non-existent outdated cache."""
        cached = cache.get_cached_outdated("https://github.com/nonexistent/repo")
        assert cached is None

    # Cache Expiration Tests
    def test_cache_expiration(self, temp_cache_dir):
        """Test that expired cache returns None."""
        # Create cache with 0 hours expiry (immediately expires)
        cache = RepositoryCache(cache_dir=temp_cache_dir, expiry_hours=0)

        repo_url = "https://github.com/test/project"
        analysis_data = {"test": "data"}

        cache.cache_analysis(repo_url, analysis_data)

        # Wait a moment and check - should be expired
        time.sleep(0.1)
        cached = cache.get_cached_analysis(repo_url)
        assert cached is None

    def test_cache_valid_within_expiry(self, temp_cache_dir):
        """Test that cache is valid within expiry time."""
        cache = RepositoryCache(cache_dir=temp_cache_dir, expiry_hours=24)

        repo_url = "https://github.com/test/project"
        analysis_data = {"test": "data"}

        cache.cache_analysis(repo_url, analysis_data)

        # Should still be valid
        cached = cache.get_cached_analysis(repo_url)
        assert cached is not None

    # Cache Invalidation Tests
    def test_invalidate_cache(self, cache, sample_repo):
        """Test invalidating cache for a repository."""
        repo_url = "https://github.com/test/project"

        # Cache repository and analysis
        cache.cache_repository(repo_url, sample_repo)
        cache.cache_analysis(repo_url, {"test": "data"})

        # Verify cache exists
        assert cache.get_cached_repository(repo_url) is not None

        # Invalidate
        cache.invalidate_cache(repo_url)

        # Verify cache is gone
        assert cache.get_cached_repository(repo_url) is None
        assert cache.get_cached_analysis(repo_url) is None

    # Cleanup Tests
    def test_cleanup_expired(self, temp_cache_dir):
        """Test cleanup of expired cache entries."""
        cache = RepositoryCache(cache_dir=temp_cache_dir, expiry_hours=0)

        # Create some cache entries
        for i in range(3):
            cache.cache_analysis(f"https://github.com/test/project{i}", {"test": i})

        # Wait for expiration
        time.sleep(0.1)

        # Cleanup
        removed = cache.cleanup_expired()
        assert removed == 3

    def test_clear_all(self, cache, sample_repo):
        """Test clearing all cache entries."""
        repo_url = "https://github.com/test/project"

        # Cache some data
        cache.cache_repository(repo_url, sample_repo)
        cache.cache_analysis(repo_url, {"test": "data"})

        # Clear all
        cache.clear_all()

        # Verify all cleared
        assert cache.get_cached_repository(repo_url) is None
        assert cache.get_cached_analysis(repo_url) is None

    # Statistics Tests
    def test_get_cache_stats(self, cache, sample_repo):
        """Test getting cache statistics."""
        repo_url = "https://github.com/test/project"

        # Cache some data
        cache.cache_repository(repo_url, sample_repo)
        cache.cache_analysis(repo_url, {"test": "data"})

        stats = cache.get_cache_stats()

        assert stats["total_entries"] >= 1
        assert stats["valid_entries"] >= 1
        assert stats["expired_entries"] >= 0
        assert "total_size_mb" in stats
        assert stats["expiry_hours"] == 24


class TestGetCache:
    """Test cases for get_cache singleton function."""

    def test_get_cache_returns_instance(self):
        """Test that get_cache returns a RepositoryCache instance."""
        cache = get_cache()
        assert isinstance(cache, RepositoryCache)

    def test_get_cache_returns_same_instance(self):
        """Test that get_cache returns the same instance (singleton)."""
        cache1 = get_cache()
        cache2 = get_cache()
        assert cache1 is cache2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
