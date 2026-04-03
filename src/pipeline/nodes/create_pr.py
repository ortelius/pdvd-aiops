"""
Create PR node — deterministic, 0 LLM tokens.

Creates a branch, pushes files, and creates a GitHub PR via MCP.
"""

from src.pipeline.state import PipelineState
from src.tools.github_tools import (
    create_branch,
    create_github_pr,
    format_pr_body,
    push_files,
)


def create_pr_node(state: PipelineState) -> dict:
    """
    Create branch → push files → create PR.

    Returns: branch_name, pr_url, final_status, final_url, final_message
    """
    repo_path = state["repo_path"]
    repo_name = state["repo_name"]
    applied_updates = state.get("applied_updates", [])
    package_manager = state.get("package_manager", "")
    build_log = state.get("build_log", "")
    test_log = state.get("test_log", "")
    has_tests = state.get("has_tests", True)
    has_test_command = state.get("has_test_command", True)
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("create_pr")

    try:
        # Step 1: Create branch
        branch_result = create_branch(repo_path)
        if tracker:
            tracker.record_tool_call("create_branch")

        if branch_result["status"] != "success":
            return {
                "final_status": "error",
                "final_message": f"Failed to create branch: {branch_result.get('message', '')}",
            }

        branch_name = branch_result["branch_name"]

        # Step 2: Push files
        push_result = push_files(repo_path, branch_name)
        if tracker:
            tracker.record_tool_call("push_files")

        if push_result["status"] == "no_changes":
            return {
                "final_status": "up_to_date",
                "final_message": "No files were modified. Dependencies may already be up to date.",
            }

        if push_result["status"] != "success":
            return {
                "final_status": "error",
                "final_message": f"Failed to push files: {push_result.get('message', '')}",
            }

        # Step 3: Create PR
        title, body = format_pr_body(
            applied_updates=applied_updates,
            package_manager=package_manager,
            build_log=build_log,
            test_log=test_log,
            has_tests=has_tests,
            has_test_command=has_test_command,
            integration_results=state.get("integration_results"),
            audit_results=state.get("audit_results"),
            detected_integrations=state.get("detected_integrations"),
        )

        pr_result = create_github_pr(repo_name, branch_name, title, body)
        if tracker:
            tracker.record_tool_call("create_github_pr")

        if pr_result["status"] == "success":
            return {
                "branch_name": branch_name,
                "pr_url": pr_result["pr_url"],
                "final_status": "pr_created",
                "final_url": pr_result["pr_url"],
                "final_message": f"PR created: {pr_result['pr_url']}",
            }
        else:
            return {
                "final_status": "error",
                "final_message": f"Failed to create PR: {pr_result.get('message', '')}",
            }

    except Exception as e:
        return {"final_status": "error", "final_message": f"PR creation failed: {str(e)}"}
    finally:
        if tracker:
            tracker.end_phase()
