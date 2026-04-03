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
                    integration_results: list[dict] = None,
                    audit_results: list[dict] = None,
                    detected_integrations: list[dict] = None,
                    verification_results: list[dict] = None) -> tuple[str, str]:
    """
    Build PR title and body from update data.

    Returns:
        (title, body)
    """
    title = f"chore(deps): update {len(applied_updates)} dependencies"

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
