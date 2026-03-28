"""
Create Issue node — deterministic, 0 LLM tokens.

Creates a GitHub Issue with details about the failed update attempt.
"""

from src.pipeline.state import PipelineState
from src.tools.github_tools import create_github_issue


def create_issue_node(state: PipelineState) -> dict:
    """
    Create a GitHub Issue documenting the failure.

    Returns: issue_url, final_status, final_url, final_message
    """
    repo_name = state["repo_name"]
    applied_updates = state.get("applied_updates", [])
    build_result = state.get("build_result", {})
    test_result = state.get("test_result", {})
    build_log = state.get("build_log", "")
    test_log = state.get("test_log", "")
    rollback_history = state.get("rollback_history", [])
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("create_issue")

    try:
        title = "Dependency update failed — manual intervention needed"

        body = "## Automated Dependency Update Report\n\n"
        body += "The automated dependency update process encountered failures "
        body += "that could not be resolved automatically.\n\n"

        # Updates attempted
        if applied_updates:
            body += "### Updates Attempted\n\n"
            body += "| Package | Change |\n|---------|--------|\n"
            for u in applied_updates:
                body += f"| {u['name']} | `{u.get('old', '?')}` → `{u['new']}` |\n"
            body += "\n"

        # Build failure
        if build_result and not build_result.get("succeeded", True):
            body += "### Build Failure\n\n"
            body += f"```\n{build_log[-2000:]}\n```\n\n"

        # Test failure
        if test_result and not test_result.get("succeeded", True):
            body += "### Test Failure\n\n"
            body += f"```\n{test_log[-2000:]}\n```\n\n"

        # Rollback history
        if rollback_history:
            body += "### Rollback Attempts\n\n"
            for rb in rollback_history:
                body += f"- Rolled back **{rb.get('package', '?')}** "
                body += f"from `{rb.get('from_version', '?')}` to `{rb.get('to_version', '?')}`\n"
            body += "\n"

        body += "---\n*This issue was created automatically by the AI dependency updater.*\n"

        result = create_github_issue(repo_name, title, body)

        if tracker:
            tracker.record_tool_call("create_github_issue")

        if result["status"] == "success":
            return {
                "issue_url": result["issue_url"],
                "final_status": "issue_created",
                "final_url": result["issue_url"],
                "final_message": f"Issue created: {result['issue_url']}",
            }
        else:
            return {
                "final_status": "issue_failed",
                "final_message": f"Failed to create issue: {result.get('message', '')}",
            }

    except Exception as e:
        return {"final_status": "error", "final_message": f"Issue creation failed: {str(e)}"}
    finally:
        if tracker:
            tracker.end_phase()
