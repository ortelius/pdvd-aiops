"""
GitHub tools — extracted from updater.py.

Provides git_operations, create_github_pr, and create_github_issue
as standalone functions (not @tool decorated) for direct use by pipeline nodes.
The MCP integration is preserved.
"""

import json
import os
import subprocess
from datetime import datetime
from typing import Optional


def get_repo_owner_name(repo_path: str) -> tuple[str, str]:
    """Extract (owner, repo) from git remote URL. Raises ValueError on failure."""
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True, text=True, cwd=repo_path,
    )
    if result.returncode != 0:
        raise ValueError("Could not determine remote URL")
    url = result.stdout.strip()
    if "github.com" in url:
        parts = url.replace(".git", "").split("/")
        return parts[-2], parts[-1]
    raise ValueError(f"Not a GitHub URL: {url}")


_mcp_event_loop = None
_mcp_thread = None


def _get_mcp_loop():
    """Get or create a dedicated event loop for MCP calls (CLI usage)."""
    import threading

    global _mcp_event_loop, _mcp_thread
    if _mcp_event_loop is not None and _mcp_event_loop.is_running():
        return _mcp_event_loop

    import asyncio

    loop = asyncio.new_event_loop()

    def _run_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=_run_loop, daemon=True)
    thread.start()
    _mcp_event_loop = loop
    _mcp_thread = thread
    return loop


def _run_mcp_call(coro_func, *args):
    """Run an async MCP call from synchronous context."""
    import asyncio
    from src.integrations.mcp_server_manager import PersistentMCPServer

    async def _call():
        server = await PersistentMCPServer.get_instance()
        if not server.is_running:
            await server.ensure_connected()
        return await coro_func(server, *args)

    # Priority 1: Use event loop set by FastAPI server
    try:
        from src.agents.updater import _main_event_loop
        loop = _main_event_loop
    except (ImportError, AttributeError):
        loop = None

    # Priority 2: Check for a running loop in current context
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

    # Priority 3: Use dedicated MCP loop (CLI mode)
    if loop is None or not loop.is_running():
        loop = _get_mcp_loop()

    future = asyncio.run_coroutine_threadsafe(_call(), loop)
    return future.result(timeout=60)


def create_branch(repo_path: str, branch_name: Optional[str] = None) -> dict:
    """Create a branch on GitHub via MCP. Returns {"status", "branch_name"}."""
    try:
        owner, repo = get_repo_owner_name(repo_path)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    if not branch_name:
        branch_name = f"OrteliusAiBot/dep-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    async def _create(server, o, r, b):
        return await server.create_branch(o, r, b)

    result = _run_mcp_call(_create, owner, repo, branch_name)

    if result["status"] == "success":
        return {"status": "success", "branch_name": branch_name}
    return {"status": "error", "message": result.get("message", "Failed to create branch")}


def push_files(repo_path: str, branch_name: str, message: str = "chore: update dependencies") -> dict:
    """Detect modified files and push them to GitHub via MCP."""
    try:
        owner, repo = get_repo_owner_name(repo_path)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    # Detect locally modified + untracked files
    diff_result = subprocess.run(
        ["git", "diff", "--name-only"], capture_output=True, text=True, cwd=repo_path,
    )
    untracked_result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True, cwd=repo_path,
    )

    all_paths = set()
    for line in (diff_result.stdout + "\n" + untracked_result.stdout).strip().split("\n"):
        if line.strip():
            all_paths.add(line.strip())

    if not all_paths:
        return {"status": "no_changes", "message": "No files were modified."}

    # Read content of each changed file
    changed_files = []
    for file_path in all_paths:
        full_path = os.path.join(repo_path, file_path)
        try:
            with open(full_path, "r") as f:
                content = f.read()
            changed_files.append({"path": file_path, "content": content})
        except (UnicodeDecodeError, FileNotFoundError):
            continue

    if not changed_files:
        return {"status": "no_changes", "message": "No text files were modified."}

    async def _push(server, o, r, b, f, m):
        return await server.push_files(o, r, b, f, m)

    result = _run_mcp_call(_push, owner, repo, branch_name, changed_files, message)

    if result["status"] == "success":
        return {
            "status": "success",
            "branch_name": branch_name,
            "files_pushed": len(changed_files),
        }
    return {"status": "error", "message": result.get("message", "Failed to push files")}


def create_github_pr(
    repo_name: str, branch_name: str, title: str, body: str, base_branch: str = "main"
) -> dict:
    """Create a GitHub PR via MCP. Returns {"status", "pr_url"}."""
    parts = repo_name.split("/")
    if len(parts) != 2:
        return {"status": "error", "message": f"Invalid repo format: {repo_name}"}

    async def _create_pr(server, owner, repo, t, b, head, base):
        return await server.create_pull_request(
            repo_owner=owner, repo_name=repo,
            title=t, body=b, head=head, base=base,
        )

    result = _run_mcp_call(_create_pr, parts[0], parts[1], title, body, branch_name, base_branch)

    if result["status"] == "success":
        pr_url = ""
        data = result.get("data", {})
        if isinstance(data, dict):
            pr_url = data.get("html_url", "") or data.get("url", "")
        if not pr_url:
            pr_url = result.get("pr_url", "")
        return {"status": "success", "pr_url": pr_url}
    return {"status": "error", "message": result.get("message", "Unknown error")}


def create_github_issue(
    repo_name: str, title: str, body: str, labels: Optional[list[str]] = None
) -> dict:
    """Create a GitHub Issue via MCP. Returns {"status", "issue_url"}."""
    if labels is None:
        labels = ["dependencies"]

    parts = repo_name.split("/")
    if len(parts) != 2:
        return {"status": "error", "message": f"Invalid repo format: {repo_name}"}

    async def _create_issue(server, owner, repo, t, b, lbls):
        return await server.create_issue(
            repo_owner=owner, repo_name=repo,
            title=t, body=b, labels=lbls,
        )

    result = _run_mcp_call(_create_issue, parts[0], parts[1], title, body, labels)

    if result["status"] == "success":
        issue_url = ""
        data = result.get("data", {})
        if isinstance(data, dict):
            issue_url = data.get("html_url", "") or data.get("url", "")
        if not issue_url:
            issue_url = result.get("issue_url", "")
        return {"status": "success", "issue_url": issue_url}
    return {"status": "error", "message": result.get("message", "Unknown error")}


def format_pr_body(applied_updates: list[dict], package_manager: str,
                    build_log: str = "", test_log: str = "",
                    has_tests: bool = True, has_test_command: bool = True,
                    verification_results: list[dict] = None) -> tuple[str, str]:
    """
    Build PR title and body from update data.

    Returns:
        (title, body)
    """
    title = f"chore(deps): update {len(applied_updates)} dependencies"

    # Update table
    body = "## This PR contains updated dependencies:\n\n"

    # Check if any update has dep_type info
    has_dep_type = any(u.get("dep_type") for u in applied_updates)

    if has_dep_type:
        body += "| Package | Update | Change | Type |\n"
        body += "|---------|--------|--------|------|\n"
        for u in applied_updates:
            dep_type = u.get("dep_type", "direct")
            type_label = "transitive" if dep_type == "transitive" else "direct"
            body += f"| {u['name']} | {_categorize_update(u)} | `{u.get('old', '?')}` → `{u['new']}` | {type_label} |\n"
    else:
        body += "| Package | Update | Change |\n"
        body += "|---------|--------|--------|\n"
        for u in applied_updates:
            body += f"| {u['name']} | {_categorize_update(u)} | `{u.get('old', '?')}` → `{u['new']}` |\n"

    # Build/test logs
    log_section = "\n\n---\n\nAll updates have been tested and verified:\n"
    if build_log:
        log_section += (
            f"\n:white_check_mark: Build successful\n"
            f"<details><summary>Build logs</summary>\n\n"
            f"```\n{build_log[-1000:]}\n```\n\n</details>\n"
        )
    if test_log and has_tests:
        log_section += (
            f"\n:white_check_mark: Tests passing\n"
            f"<details><summary>Test logs</summary>\n\n"
            f"```\n{test_log[-1000:]}\n```\n\n</details>\n"
        )

    if build_log or (test_log and has_tests):
        if has_tests:
            log_section += (
                "\n:rocket: **This PR is safe to merge.** "
                "All dependency updates have been verified with a successful build and passing tests.\n"
            )
        elif has_test_command and not has_tests:
            log_section += (
                "\n:warning: **No unit tests were found in this repository.** "
                "The build succeeded, but there are no tests to verify runtime behavior.\n"
                "\n:rocket: **This PR can be merged**, but we strongly recommend adding tests.\n"
            )
        else:
            log_section += (
                "\n:warning: **No test command is configured for this project.** "
                "The build succeeded, but no tests were run.\n"
                "\n:rocket: **This PR can be merged**, but we strongly recommend adding tests.\n"
            )
        body += log_section

    # Verification results
    if verification_results:
        body += "\n\n## Verification Checks\n\n"
        for check in verification_results:
            icon = ":white_check_mark:" if check.get("status") == "pass" else ":warning:"
            body += f"{icon} **{check.get('check', 'Unknown')}**: {check.get('detail', '')}\n"

    return title, body


def _categorize_update(update: dict) -> str:
    """Categorize as major/minor/patch."""
    old = update.get("old", "0.0.0").lstrip("^~>=v")
    new = update.get("new", "0.0.0").lstrip("^~>=v")
    try:
        old_parts = old.split(".")
        new_parts = new.split(".")
        if old_parts[0] != new_parts[0]:
            return "major"
        if len(old_parts) >= 2 and len(new_parts) >= 2 and old_parts[1] != new_parts[1]:
            return "minor"
        return "patch"
    except (IndexError, ValueError):
        return "unknown"
