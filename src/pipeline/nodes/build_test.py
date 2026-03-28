"""
Build and Test nodes — deterministic, 0 LLM tokens.

Runs build and test commands, captures output for downstream nodes.
Uses the ecosystem plugin's fix_command() to ensure commands work on the system.
"""

import os
import subprocess
import sys

from src.ecosystems import get_plugin_by_name
from src.pipeline.state import PipelineState


def _get_env():
    env = os.environ.copy()
    python_bin = os.path.dirname(sys.executable)
    if python_bin not in env.get("PATH", ""):
        env["PATH"] = python_bin + os.pathsep + env.get("PATH", "")
    return env


# Patterns that indicate no tests were found
_NO_TESTS_PATTERNS = [
    "no test files",
    "no tests ran",
    "no tests collected",
    "collected 0 items",
    "no test suites found",
    "0 specs, 0 failures",
    "no tests found",
    "0 passing",
]


def build_node(state: PipelineState) -> dict:
    """Run the build command. Returns: build_result, build_log."""
    build_commands = state.get("build_commands", {})
    repo_path = state["repo_path"]
    package_manager = state.get("package_manager", "")
    tracker = state.get("cost_tracker")

    build_cmd = build_commands.get("build")
    if not build_cmd:
        return {
            "build_result": {"succeeded": True, "exit_code": 0, "stdout": "", "stderr": ""},
            "build_log": "",
        }

    # Let the plugin fix the command for the current system
    plugin = get_plugin_by_name(package_manager)
    if plugin:
        build_cmd = plugin.fix_command(build_cmd)

    if tracker:
        tracker.start_phase("build")

    try:
        print(f"  [build] Running: {build_cmd}")
        result = _run_command(repo_path, build_cmd)

        log_entry = f"$ {build_cmd}\n"
        combined = (result["stdout"] + "\n" + result["stderr"]).strip()
        log_entry += combined if combined else f"exit code: {result['exit_code']}"

        if tracker:
            tracker.record_tool_call("run_build", build_cmd)

        status = "PASS" if result["succeeded"] else "FAIL"
        print(f"  [build] {status} (exit code {result['exit_code']})")
        if not result["succeeded"] and result.get("stderr"):
            print(f"  [build] Error: {result['stderr'][-200:]}")

        return {
            "build_result": result,
            "build_log": log_entry,
        }
    finally:
        if tracker:
            tracker.end_phase()


def test_node(state: PipelineState) -> dict:
    """Run the test command. Returns: test_result, test_log, has_tests, has_test_command."""
    build_commands = state.get("build_commands", {})
    repo_path = state["repo_path"]
    package_manager = state.get("package_manager", "")
    tracker = state.get("cost_tracker")

    test_cmd = build_commands.get("test")
    if not test_cmd:
        return {
            "test_result": {"succeeded": True, "exit_code": 0, "stdout": "", "stderr": ""},
            "test_log": "",
            "has_tests": False,
            "has_test_command": False,
        }

    # Let the plugin fix the command for the current system
    plugin = get_plugin_by_name(package_manager)
    if plugin:
        test_cmd = plugin.fix_command(test_cmd)

    if tracker:
        tracker.start_phase("test")

    try:
        print(f"  [test] Running: {test_cmd}")
        result = _run_command(repo_path, test_cmd)

        log_entry = f"$ {test_cmd}\n"
        combined = (result["stdout"] + "\n" + result["stderr"]).strip()
        log_entry += combined if combined else f"exit code: {result['exit_code']}"

        # Detect if repo has no tests
        combined_lower = combined.lower()
        has_tests = not any(pat in combined_lower for pat in _NO_TESTS_PATTERNS)

        if tracker:
            tracker.record_tool_call("run_test", test_cmd)

        status = "PASS" if result["succeeded"] else "FAIL"
        print(f"  [test] {status} (exit code {result['exit_code']})")

        return {
            "test_result": result,
            "test_log": log_entry,
            "has_tests": has_tests,
            "has_test_command": True,
        }
    finally:
        if tracker:
            tracker.end_phase()


def _run_command(repo_path: str, command: str, timeout: int = 300) -> dict:
    """Execute a command and return structured result."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=repo_path, env=_get_env(),
        )
        return {
            "succeeded": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-2000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {
            "succeeded": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds",
        }
    except Exception as e:
        return {
            "succeeded": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
        }
