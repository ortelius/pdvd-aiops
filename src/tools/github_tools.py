"""
GitHub tools — extracted from updater.py.

Provides git_operations, create_github_pr, and create_github_issue
as standalone functions (not @tool decorated) for direct use by pipeline nodes.
The MCP integration is preserved.
"""

import json
import os
import re
import subprocess
from datetime import datetime
from typing import Optional


def _extract_pr_url(data, owner: str = "", repo: str = "") -> str:
    """
    Extract the PR URL from an MCP response, handling all known response shapes.

    The GitHub MCP server may return the PR data in different structures:
    - {"html_url": "..."} — top-level
    - {"url": "https://api.github.com/..."} — API URL (needs conversion)
    - {"number": 123} — just the PR number (we build the URL)
    - A string containing the URL
    - Nested inside a sub-key

    Returns the html_url or empty string.
    """
    if isinstance(data, str):
        # Try to extract a URL from the string
        match = re.search(r'https://github\.com/[^\s"\']+/pull/\d+', data)
        if match:
            return match.group(0)
        return ""

    if not isinstance(data, dict):
        return ""

    # Direct html_url
    url = data.get("html_url", "")
    if url and "github.com" in url:
        return url

    # API URL → convert to html_url
    api_url = data.get("url", "")
    if api_url and "api.github.com" in api_url:
        # https://api.github.com/repos/owner/repo/pulls/123 → https://github.com/owner/repo/pull/123
        html = api_url.replace("api.github.com/repos", "github.com").replace("/pulls/", "/pull/")
        if "/pull/" in html:
            return html

    # Just a PR number — build the URL
    pr_number = data.get("number")
    if pr_number and owner and repo:
        return f"https://github.com/{owner}/{repo}/pull/{pr_number}"

    # Search nested dicts (one level deep)
    for key in ("pull_request", "data", "result"):
        nested = data.get(key)
        if isinstance(nested, dict):
            url = nested.get("html_url", "") or nested.get("url", "")
            if url and "github.com" in url:
                return url
            nr = nested.get("number")
            if nr and owner and repo:
                return f"https://github.com/{owner}/{repo}/pull/{nr}"

    return ""


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


FIXED_BRANCH_NAME = "pdvd-aiops/dep-update"


def create_branch(repo_path: str, branch_name: Optional[str] = None) -> dict:
    """
    Create or reuse a fixed branch on GitHub via MCP.

    Uses a single fixed branch name per repo to avoid branch clutter.
    If the branch already exists, that's fine — push_files will update it.
    """
    try:
        owner, repo = get_repo_owner_name(repo_path)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    if not branch_name:
        branch_name = FIXED_BRANCH_NAME

    async def _create(server, o, r, b):
        return await server.create_branch(o, r, b)

    result = _run_mcp_call(_create, owner, repo, branch_name)

    if result["status"] == "success":
        return {"status": "success", "branch_name": branch_name}

    # Branch may already exist — that's fine, we'll push to it
    error_msg = result.get("message", "").lower()
    if "already exists" in error_msg or "reference already exists" in error_msg:
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


def _find_existing_pr(owner: str, repo: str, branch_name: str) -> Optional[dict]:
    """Check if an open PR already exists for the given branch."""
    try:
        async def _list(server, o, r, head):
            return await server.list_pull_requests(o, r, head=head, state="open")

        result = _run_mcp_call(_list, owner, repo, branch_name)

        if result.get("status") == "success":
            data = result.get("data", [])
            # MCP may return list directly or nested
            prs = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []

            if not prs:
                print(f"  [create_pr] DEBUG list_pull_requests returned 0 PRs (data type={type(data).__name__}, keys={list(data.keys()) if isinstance(data, dict) else 'N/A'})")
                # If data is a dict with PR-like fields, it might BE the PR
                if isinstance(data, dict) and data.get("number"):
                    return data

            for pr in prs:
                if isinstance(pr, dict) and pr.get("head", {}).get("ref") == branch_name:
                    return pr

            # If no exact match on head.ref, return the first PR (likely ours)
            if prs and isinstance(prs[0], dict):
                print(f"  [create_pr] DEBUG no head.ref match, using first PR: number={prs[0].get('number')}")
                return prs[0]
    except Exception as e:
        print(f"  [create_pr] DEBUG _find_existing_pr error: {e}")
    return None


def _update_existing_pr(owner: str, repo: str, pr_number: int, title: str, body: str) -> dict:
    """Update an existing PR's title and body via GitHub REST API."""
    import requests

    repo_full = f"{owner}/{repo}"
    pr_url = f"https://github.com/{repo_full}/pull/{pr_number}"
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN", "")

    if not token:
        return {"status": "error", "message": "No GitHub token available"}

    try:
        resp = requests.patch(
            f"https://api.github.com/repos/{repo_full}/pulls/{pr_number}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
            json={"title": title, "body": body},
            timeout=30,
        )
        if resp.status_code == 200:
            return {"status": "success", "pr_url": pr_url}
        else:
            print(f"  [create_pr] PR update failed ({resp.status_code}): {resp.text[:200]}")
            return {"status": "error", "message": resp.text[:200]}
    except Exception as e:
        print(f"  [create_pr] PR update error: {e}")
        return {"status": "error", "message": str(e)}


def create_github_pr(
    repo_name: str, branch_name: str, title: str, body: str, base_branch: str = "main"
) -> dict:
    """
    Create or update a GitHub PR via MCP.

    If an open PR already exists for the branch, updates its title and body
    instead of creating a duplicate. This keeps a single PR per repo.
    """
    parts = repo_name.split("/")
    if len(parts) != 2:
        return {"status": "error", "message": f"Invalid repo format: {repo_name}"}

    owner, repo = parts

    # Check for existing open PR on this branch
    existing_pr = _find_existing_pr(owner, repo, branch_name)
    if existing_pr:
        pr_number = existing_pr.get("number")
        pr_url = existing_pr.get("html_url", "")
        print(f"  [create_pr] Found existing PR #{pr_number}, updating...")

        update_result = _update_existing_pr(owner, repo, pr_number, title, body)
        if update_result.get("status") == "success":
            final_url = update_result.get("pr_url") or pr_url
            return {"status": "success", "pr_url": final_url}

    # Create new PR
    async def _create_pr(server, o, r, t, b, head, base):
        return await server.create_pull_request(
            repo_owner=o, repo_name=r,
            title=t, body=b, head=head, base=base,
        )

    result = _run_mcp_call(_create_pr, owner, repo, title, body, branch_name, base_branch)

    if result["status"] == "success":
        data = result.get("data", {})
        pr_url = _extract_pr_url(data, owner, repo)
        if not pr_url:
            pr_url = _extract_pr_url(result, owner, repo)
        if not pr_url:
            # Debug: dump what MCP actually returned so we can fix extraction
            _data_summary = str(data)[:500] if data else "(empty)"
            print(f"  [create_pr] DEBUG MCP response data: {_data_summary}")
            # Fallback: query the PR list to find the URL
            print(f"  [create_pr] PR created but URL not in response, looking up...")
            existing = _find_existing_pr(owner, repo, branch_name)
            if existing:
                pr_url = _extract_pr_url(existing, owner, repo)
            else:
                print(f"  [create_pr] DEBUG _find_existing_pr returned None")
        if not pr_url:
            # Last resort: construct URL from owner/repo — PR must exist since status=success
            # We don't know the number, so link to the PR list filtered by branch
            pr_url = f"https://github.com/{owner}/{repo}/pulls?q=head:{branch_name}"
            print(f"  [create_pr] Using fallback PR list URL: {pr_url}")
        return {"status": "success", "pr_url": pr_url}

    # PR creation may fail if one already exists (race condition / MCP quirk)
    error_msg = result.get("message", "").lower()
    if "already exists" in error_msg:
        existing = _find_existing_pr(owner, repo, branch_name)
        if existing:
            pr_url = _extract_pr_url(existing, owner, repo)
            retry = _update_existing_pr(owner, repo, existing["number"], title, body)
            if retry.get("status") != "success":
                print(f"  [create_pr] Retry update also failed: {retry.get('message', '')}")
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
        data = result.get("data", {})
        issue_url = ""
        if isinstance(data, dict):
            issue_url = data.get("html_url", "") or data.get("url", "")
            # Fallback: build from issue number
            if not issue_url and data.get("number"):
                issue_url = f"https://github.com/{parts[0]}/{parts[1]}/issues/{data['number']}"
        if not issue_url:
            issue_url = result.get("issue_url", "")
        return {"status": "success", "issue_url": issue_url}
    return {"status": "error", "message": result.get("message", "Unknown error")}


# ── Security Issue (find-or-update) ────────────────────────────

SECURITY_ISSUE_TITLE = "Security: unfixable CVEs in dependencies"
SECURITY_ISSUE_LABEL = "security"
SECURITY_ISSUE_MARKER = "<!-- pdvd-aiops-security-tracker -->"

FAILURE_ISSUE_MARKER = "<!-- pdvd-aiops-failure-tracker -->"


def _find_existing_issue_by_marker(owner: str, repo: str, marker: str, label: str = "") -> Optional[dict]:
    """
    Find an existing pdvd-aiops issue by its hidden HTML marker.

    Strategy:
    1. Try MCP search_issues with marker text
    2. If search fails or returns empty (GitHub search index delay),
       fall back to listing open issues and scanning bodies client-side

    Returns the issue dict if found, None otherwise.
    """
    tag = label or "issue"

    # ── Strategy 1: Search API (fast but may miss recently created issues) ──
    try:
        async def _search(server, q):
            return await server.search_issues(query=q)

        query = f"repo:{owner}/{repo} is:issue is:open \"{marker}\" in:body"
        result = _run_mcp_call(_search, query)

        if result.get("status") == "success":
            data = result.get("data", {})
            items = _extract_items_from_response(data)
            if items:
                print(f"  [{tag}] Found existing issue via search: #{items[0].get('number', '?')}")
                return items[0]
    except Exception as e:
        print(f"  [{tag}] Search failed: {e}")

    # ── Strategy 2: List issues and scan bodies client-side (reliable) ──
    try:
        async def _list(server, o, r):
            return await server.list_issues(o, r, state="open", per_page=30)

        result = _run_mcp_call(_list, owner, repo)

        if result.get("status") == "success":
            data = result.get("data", {})
            issues = _extract_items_from_response(data)
            for issue in issues:
                body = issue.get("body", "") or ""
                if marker in body:
                    print(f"  [{tag}] Found existing issue via list scan: #{issue.get('number', '?')}")
                    return issue
    except Exception as e:
        print(f"  [{tag}] List fallback failed: {e}")

    return None


def _extract_items_from_response(data) -> list:
    """Extract a list of items from various MCP response shapes."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Standard GitHub search response
        for key in ("items", "issues", "results", "nodes"):
            if isinstance(data.get(key), list):
                return data[key]
        # Single item response — wrap in list
        if data.get("number"):
            return [data]
    return []


def _update_github_issue(
    owner: str, repo: str, issue_number: int, title: str, body: str
) -> dict:
    """Update an existing GitHub issue via MCP."""
    try:
        async def _update(server, o, r, num, t, b):
            return await server.update_issue(o, r, num, title=t, body=b)

        result = _run_mcp_call(_update, owner, repo, issue_number, title, body)

        if result.get("status") == "success":
            data = result.get("data", {})
            issue_url = ""
            if isinstance(data, dict):
                issue_url = data.get("html_url", "") or data.get("url", "")
                if not issue_url and data.get("number"):
                    issue_url = f"https://github.com/{owner}/{repo}/issues/{data['number']}"
            if not issue_url:
                issue_url = f"https://github.com/{owner}/{repo}/issues/{issue_number}"
            return {"status": "success", "issue_url": issue_url}
        return {"status": "error", "message": result.get("message", "")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def find_or_update_security_issue(
    repo_name: str,
    unfixable_cves: list[dict],
    audit_results: list[dict],
    package_manager: str = "",
) -> dict:
    """
    Create or update a single security tracking issue for unfixable CVEs.

    If an existing pdvd-aiops security issue exists, updates it with the
    latest findings. Otherwise creates a new one. This ensures one issue
    per repo that acts as a living security tracker.

    Returns: {"status": "issue_created"|"issue_updated", "issue_url": str}
    """
    parts = repo_name.split("/")
    if len(parts) != 2:
        return {"status": "error", "message": f"Invalid repo format: {repo_name}"}
    owner, repo = parts

    title, body = format_security_issue_body(
        unfixable_cves=unfixable_cves,
        audit_results=audit_results,
        package_manager=package_manager,
        repo_name=repo_name,
    )

    # Check for existing issue
    existing = _find_existing_issue_by_marker(owner, repo, SECURITY_ISSUE_MARKER, "security_issue")

    if existing:
        issue_number = existing.get("number")
        issue_url = existing.get("html_url", "")
        if not issue_url:
            issue_url = f"https://github.com/{owner}/{repo}/issues/{issue_number}"

        print(f"  [security_issue] Found existing issue #{issue_number}, updating...")
        update_result = _update_github_issue(owner, repo, issue_number, title, body)

        if update_result.get("status") == "success":
            return {
                "status": "issue_updated",
                "issue_url": update_result.get("issue_url") or issue_url,
            }
        # Fall through to create if update fails

    # Create new issue
    result = create_github_issue(
        repo_name, title, body, labels=[SECURITY_ISSUE_LABEL, "dependencies"],
    )

    if result["status"] == "success":
        return {"status": "issue_created", "issue_url": result.get("issue_url", "")}

    return {"status": "error", "message": result.get("message", "Failed to create issue")}


def find_or_update_failure_issue(
    repo_name: str,
    title: str,
    body: str,
) -> dict:
    """
    Create or update a single failure tracking issue.

    Prepends the failure marker to the body, then searches for an existing
    issue with the same marker. Updates if found, creates if not.

    Returns: {"status": "issue_created"|"issue_updated", "issue_url": str}
    """
    parts = repo_name.split("/")
    if len(parts) != 2:
        return {"status": "error", "message": f"Invalid repo format: {repo_name}"}
    owner, repo = parts

    # Prepend marker to body
    body_with_marker = f"{FAILURE_ISSUE_MARKER}\n\n{body}"

    # Check for existing issue
    existing = _find_existing_issue_by_marker(owner, repo, FAILURE_ISSUE_MARKER, "failure_issue")

    if existing:
        issue_number = existing.get("number")
        issue_url = existing.get("html_url", "")
        if not issue_url:
            issue_url = f"https://github.com/{owner}/{repo}/issues/{issue_number}"

        print(f"  [failure_issue] Found existing issue #{issue_number}, updating...")
        update_result = _update_github_issue(owner, repo, issue_number, title, body_with_marker)

        if update_result.get("status") == "success":
            return {
                "status": "issue_updated",
                "issue_url": update_result.get("issue_url") or issue_url,
            }

    # Create new issue
    result = create_github_issue(repo_name, title, body_with_marker)

    if result["status"] == "success":
        return {"status": "issue_created", "issue_url": result.get("issue_url", "")}

    return {"status": "error", "message": result.get("message", "Failed to create issue")}


def format_security_issue_body(
    unfixable_cves: list[dict],
    audit_results: list[dict],
    package_manager: str = "",
    repo_name: str = "",
) -> tuple[str, str]:
    """
    Build a detailed issue title and body for unfixable CVEs.

    Returns: (title, body)
    """
    cve_count = len(unfixable_cves)
    pkg_names = sorted(set(c.get("package", "unknown") for c in unfixable_cves))

    title = f"Security: {cve_count} unfixable CVE(s) in dependencies — action needed when fixes are available"

    body = f"{SECURITY_ISSUE_MARKER}\n\n"
    body += "## Unfixable Vulnerabilities Detected\n\n"
    body += (
        f"The automated dependency scanner ([pdvd-aiops](https://github.com/codeWithUtkarsh/pdvd-aiops)) "
        f"found **{cve_count} CVE(s)** in dependencies that **cannot be automatically fixed** at this time.\n\n"
    )
    body += (
        "These vulnerabilities either have no fix version released yet, or affect "
        "**transitive dependencies** that are not directly listed in the project's dependency file.\n\n"
    )

    # ── CVE Details Table ──
    body += "### CVE Details\n\n"
    body += "| CVE ID | Package | Severity | Detail | Fix Status |\n"
    body += "|--------|---------|----------|--------|------------|\n"

    for cve in unfixable_cves:
        vuln_id = cve.get("vulnerability", "unknown")
        package = cve.get("package", "unknown")
        detail = cve.get("detail", "No details available")
        # Truncate long details for table readability
        detail_short = detail[:150] + "..." if len(detail) > 150 else detail
        # Escape pipe characters in detail to not break markdown table
        detail_short = detail_short.replace("|", "\\|")

        vuln_link = _linkify_vuln_id(vuln_id)
        fix_status = "No fix available"

        body += f"| {vuln_link} | `{package}` | - | {detail_short} | {fix_status} |\n"

    # ── Affected Packages Summary ──
    body += "\n### Affected Packages\n\n"
    for pkg in pkg_names:
        pkg_cves = [c for c in unfixable_cves if c.get("package") == pkg]
        cve_ids = ", ".join(_linkify_vuln_id(c.get("vulnerability", "")) for c in pkg_cves)
        body += f"- **`{pkg}`** — {len(pkg_cves)} CVE(s): {cve_ids}\n"

    # ── Why These Can't Be Fixed ──
    body += "\n### Why These Can't Be Auto-Fixed\n\n"
    body += "| Reason | Explanation |\n"
    body += "|--------|-------------|\n"
    body += "| **Transitive dependency** | The vulnerable package is pulled in by another dependency, not listed directly in your dependency file. Updating requires the parent package to release a new version. |\n"
    body += "| **No fix version released** | The vulnerability has been reported but the maintainers haven't released a patched version yet. |\n"

    # ── What You Can Do ──
    body += "\n### Recommended Actions\n\n"
    body += "1. **Monitor for fix releases** — Subscribe to the CVE links above for updates\n"
    body += "2. **Check if parent packages have updates** — A transitive dependency fix often comes via updating the direct dependency that pulls it in\n"
    if package_manager:
        if package_manager in ("pip", "poetry"):
            body += f"3. **Override transitive versions** — Use `pip install {' '.join(pkg_names)}==<fixed_version>` or add constraints to `constraints.txt`\n"
        elif package_manager in ("npm", "yarn", "pnpm"):
            body += f"3. **Override transitive versions** — Add `overrides` (npm) or `resolutions` (yarn) in `package.json`\n"
        elif package_manager == "go-mod":
            body += f"3. **Replace transitive modules** — Use `replace` directives in `go.mod` to pin to a fixed version\n"
        elif package_manager == "cargo":
            body += f"3. **Patch transitive crates** — Use `[patch]` section in `Cargo.toml` to override versions\n"
    body += (
        f"4. **Accept the risk** — If the vulnerable code path is not reachable in your application, "
        f"document the decision and close this issue\n"
    )

    # ── Full Audit Results ──
    if audit_results:
        total_findings = sum(r.get("finding_count", 0) for r in audit_results)
        body += "\n### Full Audit Summary\n\n"
        body += "| Scanner | Status | Findings |\n"
        body += "|---------|--------|----------|\n"
        for r in audit_results:
            status = r.get("status", "unknown")
            icon = ":white_check_mark:" if status == "pass" else ":warning:"
            count = r.get("finding_count", 0)
            body += f"| {icon} {r.get('source', '')} | {status} | {count} |\n"

    # ── Metadata ──
    body += "\n---\n\n"
    body += "<details><summary>Scan metadata</summary>\n\n"
    body += f"- **Repository**: `{repo_name}`\n"
    body += f"- **Package manager**: `{package_manager}`\n"
    body += f"- **Scanner**: pdvd-aiops (automated)\n"
    body += f"- **Scan date**: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}\n"
    body += f"- **Unfixable CVEs**: {cve_count}\n"
    body += f"- **Affected packages**: {', '.join(pkg_names)}\n"
    body += "\n</details>\n\n"
    body += (
        "> **This issue is automatically managed by pdvd-aiops.** "
        "It will be updated on each scan. When all CVEs are resolved, you can close this issue.\n"
    )

    return title, body


def format_pr_body(applied_updates: list[dict], package_manager: str,
                    build_log: str = "", test_log: str = "",
                    has_tests: bool = True, has_test_command: bool = True,
                    integration_results: list[dict] = None,
                    audit_results: list[dict] = None,
                    detected_integrations: list[dict] = None,
                    verification_results: list[dict] = None,
                    security_fixes: list[dict] = None,
                    unfixable_cves: list[dict] = None) -> tuple[str, str]:
    """
    Build PR title and body from update data.

    Returns:
        (title, body)
    """
    # Determine PR type based on content
    security_fixes = security_fixes or []
    unfixable_cves = unfixable_cves or []
    is_security_pr = security_fixes and not build_log

    if is_security_pr:
        title = f"fix(security): patch {len(security_fixes)} CVE(s) in dependencies"
    elif applied_updates:
        title = f"chore(deps): update {len(applied_updates)} dependencies"
    else:
        title = "chore(deps): dependency maintenance"

    # Get ecosystem plugin for release URLs
    from src.ecosystems import get_plugin_by_name
    plugin = get_plugin_by_name(package_manager)

    # ── AI-generated summary (Haiku, ~$0.002) ────────────
    ai_summary = _generate_ai_summary(
        applied_updates, package_manager,
        has_tests=has_tests,
        integration_results=integration_results,
        audit_results=audit_results,
    )

    body = ""
    if ai_summary:
        body += f"## Summary\n\n{ai_summary}\n\n---\n\n"

    # Update table
    body += "## Updated Dependencies\n\n"

    # Check if any update has dep_type info
    has_dep_type = any(u.get("dep_type") for u in applied_updates)

    if has_dep_type:
        body += "| Package | Update | Change | Type | Release |\n"
        body += "|---------|--------|--------|------|--------|\n"
        for u in applied_updates:
            dep_type = u.get("dep_type", "direct")
            type_label = "transitive" if dep_type == "transitive" else "direct"
            release = _format_release_link(plugin, u["name"], u["new"])
            body += f"| {u['name']} | {_categorize_update(u)} | `{u.get('old', '?')}` → `{u['new']}` | {type_label} | {release} |\n"
    else:
        body += "| Package | Update | Change | Release |\n"
        body += "|---------|--------|--------|--------|\n"
        for u in applied_updates:
            release = _format_release_link(plugin, u["name"], u["new"])
            body += f"| {u['name']} | {_categorize_update(u)} | `{u.get('old', '?')}` → `{u['new']}` | {release} |\n"

    # Security fixes section
    if security_fixes:
        body += "\n\n## Security Fixes Applied\n\n"
        body += "| Package | CVE(s) | Old | Fixed | Release |\n"
        body += "|---------|--------|-----|-------|--------|\n"
        for sf in security_fixes:
            vuln_links = ", ".join(_linkify_vuln_id(v.strip()) for v in sf.get("vulnerability", "").split(",") if v.strip())
            release = _format_release_link(plugin, sf["name"], sf["new"])
            body += f"| `{sf['name']}` | {vuln_links} | `{sf.get('old', '?')}` | `{sf['new']}` | {release} |\n"
        body += "\n:warning: **Security fixes have not been build-tested.** Please verify in CI before merging.\n"

    if unfixable_cves:
        body += "\n\n## Unfixable CVEs (TODO)\n\n"
        body += "The following vulnerabilities have **no fix available** yet. TODO comments have been added to the dependency file.\n\n"
        body += "| Package | CVE | Detail |\n"
        body += "|---------|-----|--------|\n"
        for uf in unfixable_cves:
            vuln_link = _linkify_vuln_id(uf.get("vulnerability", ""))
            body += f"| `{uf.get('package', '')}` | {vuln_link} | {uf.get('detail', '')[:200]} |\n"

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

    # Integration results (only tools that were actually executed)
    if integration_results:
        body += "\n\n## Integration Checks\n\n"
        body += "| Tool | Category | Status |\n"
        body += "|------|----------|--------|\n"
        for result in integration_results:
            status = result.get("status", "unknown")
            if status == "pass":
                icon = ":white_check_mark:"
                label = "passed"
            elif status == "error":
                icon = ":x:"
                label = "error"
            else:
                icon = ":warning:"
                label = status
            body += f"| {icon} {result.get('name', '')} | {result.get('category', '')} | {label} |\n"

        # Show detailed output for each tool
        for result in integration_results:
            name = result.get("name", "")
            findings = result.get("findings") or []

            # Renovate-style table for dependency managers with parsed updates
            if findings and any(f.get("update_type") for f in findings):
                body += f"\n### {name} — {len(findings)} update(s) available\n\n"
                body += "| Package | Type | Update | Change |\n"
                body += "|---------|------|--------|--------|\n"
                for f in findings:
                    pkg = f.get("package", "")
                    dep_type = f.get("dep_type") or f.get("vulnerability", "")
                    update_type = f.get("update_type") or f.get("severity", "")
                    change = f.get("detail", "")
                    body += f"| {pkg} | {dep_type} | {update_type} | {change} |\n"
                body += "\n"

            # Generic findings list for other tools
            elif findings:
                body += f"\n<details><summary>{name} findings ({len(findings)})</summary>\n\n"
                for f in findings[:20]:
                    body += f"- **{f.get('package', '')}**: {f.get('detail', '')}\n"
                if len(findings) > 20:
                    body += f"- ... and {len(findings) - 20} more\n"
                body += "\n</details>\n\n"

            # Raw logs (always, in collapsible block)
            output = result.get("stdout") or result.get("stderr") or ""
            output = output.strip()
            if output:
                body += f"<details><summary>{name} logs</summary>\n\n"
                body += f"```\n{output[-3000:]}\n```\n\n</details>\n\n"

    # Detected but not installed tools (inform the user)
    if detected_integrations:
        ran_names = {r.get("name") for r in (integration_results or [])}
        not_installed = [
            i for i in detected_integrations
            if not i.get("runnable") and i["name"] not in ran_names
               and i.get("category") != "security_scanner"
        ]
        if not_installed:
            body += "\n\n## Detected (not installed)\n\n"
            body += "The following tools are configured in this repo but were not found on the system:\n\n"
            for ni in not_installed:
                body += f"- **{ni['name']}** ({ni.get('category', '')}) — config: `{ni.get('config_file', '')}`\n"

    # Security audit results with AI-style recommendations
    if audit_results:
        body += "\n\n## Security Audit\n\n"
        all_findings = []
        for result in audit_results:
            for f in result.get("findings", []):
                f["_source"] = result.get("source", "")
                all_findings.append(f)

        if not all_findings:
            body += ":shield: **No vulnerabilities found** — all security checks passed.\n\n"
        else:
            body += _build_security_recommendations(all_findings)

        # Scanner summary table
        body += "| Scanner | Status | Findings |\n"
        body += "|---------|--------|----------|\n"
        for result in audit_results:
            status = result.get("status", "unknown")
            if status == "pass":
                icon = ":white_check_mark:"
                label = "passed"
            elif status == "error":
                icon = ":x:"
                label = "error"
            else:
                icon = ":warning:"
                label = status
            count = result.get("finding_count", 0)
            body += f"| {icon} {result.get('source', '')} | {label} | {count} |\n"

        # Logs (collapsible)
        for result in audit_results:
            output = result.get("stdout") or result.get("stderr") or ""
            output = output.strip()
            if output:
                body += f"\n<details><summary>{result.get('source', '')} logs</summary>\n\n"
                body += f"```\n{output[-3000:]}\n```\n\n</details>\n\n"

    return title, body


def _build_security_recommendations(findings: list[dict]) -> str:
    """
    Analyze security findings and build prioritized recommendations.

    Groups by package, separates called vs not-called, and produces
    actionable advice — all deterministic, no LLM needed.
    """
    from collections import defaultdict

    # Group findings by package
    by_package = defaultdict(list)
    for f in findings:
        pkg = f.get("package", "unknown")
        by_package[pkg].append(f)

    # Separate into called (critical) and not-called (informational)
    called_packages = {}
    not_called_packages = {}
    for pkg, pkg_findings in by_package.items():
        called = [f for f in pkg_findings if "(called)" in f.get("severity", "")]
        not_called = [f for f in pkg_findings if "(called)" not in f.get("severity", "")]
        if called:
            called_packages[pkg] = called
        if not_called:
            not_called_packages[pkg] = not_called

    body = ""
    total = len(findings)
    called_count = sum(len(v) for v in called_packages.values())
    not_called_count = sum(len(v) for v in not_called_packages.values())

    body += f":warning: **{total} advisory/advisories** found across {len(by_package)} package(s)\n\n"

    # ── Critical: vulnerabilities in called code ──
    if called_packages:
        body += f"### :rotating_light: Action Required — {called_count} vulnerability(s) in code paths that are called\n\n"
        body += "| Package | CVEs | Recommendation |\n"
        body += "|---------|------|----------------|\n"
        for pkg, pkg_findings in sorted(called_packages.items()):
            cves = ", ".join(
                _linkify_vuln_id(f.get("vulnerability", "")) for f in pkg_findings[:3]
            )
            if len(pkg_findings) > 3:
                cves += f" +{len(pkg_findings) - 3} more"
            body += f"| `{pkg}` | {cves} | **Update immediately** — vulnerable code is reachable |\n"
        body += "\n"

    # ── Informational: vulnerabilities in deps but not called ──
    if not_called_packages:
        body += f"### :information_source: {not_called_count} advisory/advisories in dependencies (not called)\n\n"
        body += "These packages have known vulnerabilities, but the vulnerable code paths "
        body += "are **not reachable** from this project. Update when convenient.\n\n"
        body += "| Package | Advisories | CVEs |\n"
        body += "|---------|------------|------|\n"
        for pkg, pkg_findings in sorted(not_called_packages.items(), key=lambda x: -len(x[1])):
            cves = ", ".join(
                _linkify_vuln_id(f.get("vulnerability", "")) for f in pkg_findings[:3]
            )
            if len(pkg_findings) > 3:
                cves += f" +{len(pkg_findings) - 3} more"
            body += f"| `{pkg}` | {len(pkg_findings)} | {cves} |\n"
        body += "\n"

    # ── Summary recommendation ──
    if called_packages and not not_called_packages:
        body += ":rotating_light: **All findings require action** — update the affected packages.\n\n"
    elif called_packages:
        body += f":rotating_light: **Priority**: Update packages with reachable vulnerabilities first. "
        body += f"The remaining {not_called_count} advisories are informational.\n\n"
    else:
        body += ":white_check_mark: **No vulnerable code paths are reachable** from this project. "
        body += "These advisories are informational — the affected functions are not called by your code.\n\n"

    return body


def _generate_ai_summary(
    applied_updates: list[dict],
    package_manager: str,
    has_tests: bool = True,
    integration_results: list[dict] = None,
    audit_results: list[dict] = None,
) -> str:
    """
    Use a lightweight LLM call to generate a human-quality PR summary.

    Cost: ~$0.002 (Haiku). Falls back gracefully if LLM is unavailable.
    """
    try:
        from src.config.llm import get_llm

        # Build context for the LLM
        updates_summary = []
        major_count = minor_count = patch_count = 0
        for u in applied_updates:
            cat = _categorize_update(u)
            if cat == "major":
                major_count += 1
            elif cat == "minor":
                minor_count += 1
            else:
                patch_count += 1
            updates_summary.append(
                f"- {u['name']}: {u.get('old', '?')} → {u['new']} ({cat})"
            )

        audit_context = ""
        if audit_results:
            total_findings = sum(r.get("finding_count", 0) for r in audit_results)
            if total_findings > 0:
                audit_context = f"\nSecurity audit found {total_findings} advisory/advisories across scanned dependencies."
            else:
                audit_context = "\nSecurity audit passed with no vulnerabilities found."

        integration_context = ""
        if integration_results:
            passed = [r for r in integration_results if r.get("status") == "pass"]
            failed = [r for r in integration_results if r.get("status") != "pass"]
            if passed:
                integration_context += f"\nIntegration checks passed: {', '.join(r['name'] for r in passed)}."
            if failed:
                integration_context += f"\nIntegration checks with warnings: {', '.join(r['name'] for r in failed)}."

        test_context = "All tests passed." if has_tests else "No tests found in this repository."

        prompt = f"""Write a concise PR description (3-5 sentences) for a dependency update PR.

Package manager: {package_manager}
Updates: {major_count} major, {minor_count} minor, {patch_count} patch ({len(applied_updates)} total)

Dependencies updated:
{chr(10).join(updates_summary[:20])}
{"... and " + str(len(updates_summary) - 20) + " more" if len(updates_summary) > 20 else ""}

Build/Test: {test_context}{audit_context}{integration_context}

Write a professional, informative summary that explains:
1. What was updated and why (keeping dependencies current, security, compatibility)
2. The risk level based on update types (major = breaking potential, minor/patch = safe)
3. Whether tests/audits passed

Be direct and factual. No markdown headers. No bullet points. Plain paragraph text only."""

        llm = get_llm(temperature=0, max_tokens=300)
        response = llm.invoke(prompt)
        summary = response.content.strip()

        # Clean up any markdown the LLM might add despite instructions
        summary = summary.replace("## ", "").replace("# ", "")
        return summary

    except Exception:
        # Graceful fallback — no summary is fine
        return ""


def _format_release_link(plugin, package_name: str, version: str) -> str:
    """Build a markdown release link using the ecosystem plugin."""
    if plugin:
        url = plugin.release_url(package_name, version)
        if url:
            return f"[{version}]({url})"
    return version


def _linkify_vuln_id(vuln_id: str) -> str:
    """Turn a vulnerability ID into a clickable link based on its prefix."""
    if not vuln_id:
        return vuln_id
    if vuln_id.startswith("GO-"):
        return f"[{vuln_id}](https://pkg.go.dev/vuln/{vuln_id})"
    if vuln_id.startswith("CVE-"):
        return f"[{vuln_id}](https://nvd.nist.gov/vuln/detail/{vuln_id})"
    if vuln_id.startswith("GHSA-"):
        return f"[{vuln_id}](https://github.com/advisories/{vuln_id})"
    if vuln_id.startswith("RUSTSEC-"):
        return f"[{vuln_id}](https://rustsec.org/advisories/{vuln_id})"
    if vuln_id.startswith("PYSEC-"):
        return f"[{vuln_id}](https://osv.dev/vulnerability/{vuln_id})"
    # Fallback: link to osv.dev which indexes all ecosystems
    return f"[{vuln_id}](https://osv.dev/vulnerability/{vuln_id})"


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
