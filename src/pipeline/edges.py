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
    if state.get("final_status") == "error":
        return "end"
    if state.get("final_status") == "up_to_date":
        # No updates to apply, but still run security audit
        return "security_audit"
    return "build"


def route_after_build(state: PipelineState) -> str:
    """After build, check if it succeeded."""
    build_result = state.get("build_result", {})
    if build_result.get("succeeded", False):
        return "test"
    # Build failed → LLM analysis for failure diagnosis, then create_issue
    return "llm_analysis"


def route_after_test(state: PipelineState) -> str:
    """After test, decide: pass → verify, fail → rollback or issue."""
    test_result = state.get("test_result", {})

    # Tests passed (or no tests) → run integrations & security audit
    if test_result.get("succeeded", False):
        return "run_integrations"

    # Tests failed — can we retry?
    retry_count = state.get("retry_count", 0)
    if retry_count < MAX_RETRIES:
        return "rollback"

    # Max retries exhausted → LLM analysis for failure diagnosis, then create_issue
    return "llm_analysis"


def route_after_security_audit(state: PipelineState) -> str:
    """After security audit, decide next step."""
    # If dependency updates were applied earlier, go to LLM analysis then PR
    applied = state.get("applied_updates")
    if applied:
        return "llm_analysis"

    # If audit found fixable CVEs, apply security fixes
    audit_results = state.get("audit_results") or []
    for result in audit_results:
        for finding in result.get("findings", []):
            if finding.get("fix_versions"):
                return "apply_security_fixes"

    # No updates and no fixable CVEs — end
    return "end"


def route_after_security_fixes(state: PipelineState) -> str:
    """After applying security fixes, decide next step based on what happened."""
    # Real file changes → LLM analysis then PR
    if state.get("security_fixes_applied"):
        return "llm_analysis"
    # Unfixable CVEs with no file changes → create/update tracking issue
    if state.get("unfixable_cves"):
        return "create_issue"
    return "end"


def route_after_llm_analysis(state: PipelineState) -> str:
    """After LLM analysis, route to create_pr or create_issue."""
    # If we arrived here from the failure path (test failed, max retries)
    build_result = state.get("build_result") or {}
    test_result = state.get("test_result") or {}
    build_failed = build_result and not build_result.get("succeeded", True)
    test_failed = test_result and not test_result.get("succeeded", True)

    if build_failed or test_failed:
        return "create_issue"

    # Normal path: updates or security fixes → create PR
    return "create_pr"


def route_after_rollback(state: PipelineState) -> str:
    """After rollback, retry build or give up."""
    retry_count = state.get("retry_count", 0)
    if retry_count <= MAX_RETRIES:
        return "build"
    return "create_issue"
