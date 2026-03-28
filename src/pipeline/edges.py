"""
Conditional edge functions for the LangGraph pipeline.

Each function takes the pipeline state and returns the name of the next node.
"""

from src.pipeline.state import PipelineState

MAX_RETRIES = 3


def route_after_orchestrator(state: PipelineState) -> str:
    """Route based on the task type selected by the orchestrator."""
    task = state.get("task", "dependency_update")
    if task == "dependency_update":
        return "analyze"
    # Future routes:
    # if task == "security_scan": return "security_scan"
    # if task == "dockerfile_optimize": return "dockerfile_optimize"
    return "analyze"  # default fallback


def route_after_analyze(state: PipelineState) -> str:
    """After analysis, check if there are outdated packages."""
    if state.get("final_status") in ("up_to_date", "error"):
        return "end"
    return "detect_commands"


def route_after_prepare(state: PipelineState) -> str:
    """After prepare, check if updates were actually applied."""
    if state.get("final_status") in ("up_to_date", "error"):
        return "end"
    return "build"


def route_after_build(state: PipelineState) -> str:
    """After build, check if it succeeded."""
    build_result = state.get("build_result", {})
    if build_result.get("succeeded", False):
        return "test"
    return "create_issue"


def route_after_test(state: PipelineState) -> str:
    """After test, decide: pass → verify, fail → rollback or issue."""
    test_result = state.get("test_result", {})

    # Tests passed (or no tests)
    if test_result.get("succeeded", False):
        return "verify"

    # Tests failed — can we retry?
    retry_count = state.get("retry_count", 0)
    if retry_count < MAX_RETRIES:
        return "rollback"

    return "create_issue"


def route_after_rollback(state: PipelineState) -> str:
    """After rollback, retry build or give up."""
    retry_count = state.get("retry_count", 0)
    if retry_count <= MAX_RETRIES:
        return "build"
    return "create_issue"
