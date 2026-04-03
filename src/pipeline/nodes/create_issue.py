"""
Create Issue node — deterministic, 0 LLM tokens.

Handles two issue types:
1. Build/test failure issues (from failed dependency updates)
2. Security tracking issues (unfixable CVEs — find-or-update pattern)
"""

from src.pipeline.state import PipelineState
from src.tools.github_tools import create_github_issue, find_or_update_failure_issue, find_or_update_security_issue


def create_issue_node(state: PipelineState) -> dict:
    """
    Create or update a GitHub Issue.

    Dispatches to:
    - Security tracking issue (find-or-update) when unfixable_cves present
    - Build/test failure issue otherwise

    Returns: issue_url, final_status, final_url, final_message
    """
    unfixable_cves = state.get("unfixable_cves") or []

    # If we have unfixable CVEs and no build/test failure, this is a security tracking issue
    build_result = state.get("build_result", {})
    test_result = state.get("test_result", {})
    has_build_test_failure = (
        (build_result and not build_result.get("succeeded", True))
        or (test_result and not test_result.get("succeeded", True))
    )

    if unfixable_cves and not has_build_test_failure:
        return _create_security_issue(state)
    else:
        return _create_failure_issue(state)


def _create_security_issue(state: PipelineState) -> dict:
    """Create or update a security tracking issue for unfixable CVEs."""
    repo_name = state["repo_name"]
    unfixable_cves = state.get("unfixable_cves") or []
    audit_results = state.get("audit_results") or []
    package_manager = state.get("package_manager", "")
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("create_issue")

    try:
        result = find_or_update_security_issue(
            repo_name=repo_name,
            unfixable_cves=unfixable_cves,
            audit_results=audit_results,
            package_manager=package_manager,
        )

        if tracker:
            tracker.record_tool_call("security_issue")

        status = result.get("status", "error")
        issue_url = result.get("issue_url", "")

        if status in ("issue_created", "issue_updated"):
            action = "created" if status == "issue_created" else "updated"
            return {
                "issue_url": issue_url,
                "final_status": status,
                "final_url": issue_url,
                "final_message": f"Security tracking issue {action}: {issue_url}",
            }
        else:
            return {
                "final_status": "error",
                "final_message": f"Failed to create security issue: {result.get('message', '')}",
            }

    except Exception as e:
        return {"final_status": "error", "final_message": f"Security issue creation failed: {str(e)}"}
    finally:
        if tracker:
            tracker.end_phase()


def _create_failure_issue(state: PipelineState) -> dict:
    """Create a GitHub Issue documenting a build/test failure."""
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

        body += "---\n*This issue is automatically managed by pdvd-aiops. It will be updated on each scan.*\n"

        result = find_or_update_failure_issue(repo_name, title, body)

        if tracker:
            tracker.record_tool_call("create_or_update_failure_issue")

        status = result.get("status", "error")
        issue_url = result.get("issue_url", "")

        if status in ("issue_created", "issue_updated"):
            action = "created" if status == "issue_created" else "updated"
            return {
                "issue_url": issue_url,
                "final_status": status,
                "final_url": issue_url,
                "final_message": f"Issue {action}: {issue_url}",
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
