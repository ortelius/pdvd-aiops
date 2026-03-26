# Running Tests

This document explains how to run the test suite for the AI Agent Tool Calling application.

## Prerequisites

### 1. Install Python Dependencies

First, ensure you have all required dependencies installed:

```bash
pip install -r requirements.txt
```

### 2. Install Test Dependencies

Install pytest and any additional test dependencies:

```bash
pip install pytest pytest-cov pytest-asyncio
```

## Running Tests

### Run All Tests

To run all tests with verbose output:

```bash
# From the project root directory
pytest tests/ -v
```

### Run Specific Test Files

Run tests for a specific module:

```bash
# Test repository cache
pytest tests/test_repository_cache.py -v

# Test dependency analyzer
pytest tests/test_dependency_analyzer.py -v

# Test dependency operations
pytest tests/test_dependency_operations.py -v

# Test smart dependency updater
pytest tests/test_smart_dependency_updater.py -v

# Test GitHub MCP client
pytest tests/test_github_mcp_client.py -v
```

### Run Specific Test Classes or Methods

```bash
# Run a specific test class
pytest tests/test_repository_cache.py::TestRepositoryCache -v

# Run a specific test method
pytest tests/test_repository_cache.py::TestRepositoryCache::test_cache_repository -v
```

### Run Tests with Coverage Report

Generate a code coverage report:

```bash
# Terminal coverage report
pytest tests/ -v --cov=. --cov-report=term-missing

# HTML coverage report
pytest tests/ -v --cov=. --cov-report=html
# Open htmlcov/index.html in your browser
```

### Run Tests in Parallel

For faster test execution on multi-core systems:

```bash
pip install pytest-xdist
pytest tests/ -v -n auto
```

## Test Categories

### Unit Tests

The test suite covers the following modules:

| Module | Test File | Description |
|--------|-----------|-------------|
| `repository_cache.py` | `test_repository_cache.py` | Repository caching with TTL |
| `dependency_analyzer.py` | `test_dependency_analyzer.py` | Package manager detection, outdated checks |
| `dependency_operations.py` | `test_dependency_operations.py` | Update application, rollback, categorization |
| `smart_dependency_updater.py` | `test_smart_dependency_updater.py` | Build commands, git operations |
| `github_mcp_client.py` | `test_github_mcp_client.py` | Container runtime, MCP client |

### Test Coverage Areas

1. **Repository Cache Tests**
   - Cache key generation
   - Repository caching and retrieval
   - Analysis result caching
   - Cache expiration and TTL
   - Cache cleanup

2. **Dependency Analyzer Tests**
   - Package manager detection (npm, pip, cargo, go, etc.)
   - Dependency file reading
   - Outdated package checking
   - Working directory restoration

3. **Dependency Operations Tests**
   - Applying updates to package.json, requirements.txt, Cargo.toml
   - Rolling back major version updates
   - Version categorization (major/minor/patch)
   - Version prefix preservation

4. **Smart Dependency Updater Tests**
   - Build command detection
   - Command execution and timeout handling
   - Git operations (branch, commit, push)
   - GitHub PR/Issue creation

5. **GitHub MCP Client Tests**
   - Container runtime detection
   - Client initialization
   - Synchronous wrapper functions

## Writing New Tests

### Test File Structure

```python
#!/usr/bin/env python3
"""
Tests for the module_name module.

Brief description of what is being tested.
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from module_name import function_to_test


class TestFunctionName:
    """Test cases for function_name."""

    @pytest.fixture
    def setup_fixture(self):
        """Setup for tests."""
        # Setup code
        yield resource
        # Cleanup code

    def test_normal_case(self, setup_fixture):
        """Test the normal/expected case."""
        result = function_to_test(input)
        assert result == expected_output

    def test_edge_case(self):
        """Test edge cases."""
        pass

    def test_error_handling(self):
        """Test error handling."""
        pass
```

### Best Practices

1. **Use fixtures** for setup/teardown
2. **Use mocking** for external dependencies (subprocess, network calls)
3. **Test both success and failure** cases
4. **Clean up temporary files** in fixtures
5. **Use descriptive test names** that explain what is being tested

## Continuous Integration

### GitHub Actions Example

Create `.github/workflows/tests.yml`:

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-cov
      - name: Run tests
        run: pytest tests/ -v --cov=. --cov-report=xml
      - name: Upload coverage
        uses: codecov/codecov-action@v4
```

## Troubleshooting

### Common Issues

**Import Errors**
```bash
# Ensure you're running from the project root
cd /path/to/AiAgentToolCalling
pytest tests/ -v
```

**Module Not Found**
```bash
# Install missing dependencies
pip install -r requirements.txt
```

**Permission Errors**
```bash
# Some tests create temporary directories
# Ensure /tmp is writable or set TMPDIR
export TMPDIR=/path/to/writable/directory
```

### Debugging Tests

Run a single test with maximum verbosity:

```bash
pytest tests/test_file.py::TestClass::test_method -vvv -s
```

The `-s` flag shows print statements in the output.
