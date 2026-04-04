"""
Multi-repo intelligence — Feature #5.

When running across an org's repos, synthesizes cross-repo insights:
"5 of your 12 repos depend on vulnerable golang.org/x/net. Updating
pdvd-backend and pdvd-frontend would eliminate 28 advisories."

This is a standalone service, not a pipeline node — it runs after
multiple single-repo pipeline runs complete.

Cost: ~1 LLM call per batch.
"""

from collections import defaultdict
from typing import Optional

from src.callbacks.cost_tracker import CostTracker
from src.intelligence.base import invoke_llm


def synthesize_multi_repo(
    pipeline_results: list[dict],
    tracker: Optional[CostTracker] = None,
) -> str:
    """
    Synthesize intelligence across multiple pipeline runs.

    Args:
        pipeline_results: List of result dicts from run_pipeline(), each containing:
            - repository: str (owner/repo)
            - status: str
            - audit_results: list[dict]
            - usage: dict
            Plus any analysis fields from the intelligence layer.

        tracker: Optional cost tracker for the batch run.

    Returns:
        Markdown summary string with cross-repo insights and recommendations.
    """
    if not pipeline_results:
        return "No pipeline results to analyze."

    context = _build_cross_repo_context(pipeline_results)

    if tracker:
        tracker.start_phase("multi_repo_intelligence")

    try:
        prompt = f"""You are an engineering manager reviewing dependency health across an organization's repositories.
Analyze these pipeline results and write a cross-repo intelligence report.

{context}

Write a concise report covering:
1. **Shared vulnerabilities**: Which CVEs affect multiple repos? Updating which repos first
   would eliminate the most advisories?
2. **Common outdated dependencies**: Which packages are outdated across many repos?
   A coordinated update would reduce total work.
3. **Risk hotspots**: Which repos have the most security findings or failed updates?
4. **Recommended update order**: Which repos should be updated first for maximum impact?
5. **Org-wide security posture**: Overall health score and trend.

Be specific with repo names, package names, and CVE IDs.
Format as markdown with clear sections. Keep under 500 words."""

        summary = invoke_llm(prompt, max_tokens=700, tracker=tracker, phase_name="multi_repo")

        return summary or _build_deterministic_summary(pipeline_results)

    finally:
        if tracker:
            tracker.end_phase()


def _build_cross_repo_context(results: list[dict]) -> str:
    """Build a structured context string from multiple pipeline results."""
    total_repos = len(results)
    successful = [r for r in results if r.get("status") == "pr_created"]
    failed = [r for r in results if r.get("status") in ("error", "issue_created")]
    up_to_date = [r for r in results if r.get("status") == "up_to_date"]

    # Aggregate vulnerabilities across repos
    vuln_by_package = defaultdict(list)  # package → [(repo, vuln_id, severity)]
    repo_finding_counts = {}

    for r in results:
        repo = r.get("repository", "unknown")
        audit_results = r.get("audit_results") or []
        total_findings = sum(ar.get("finding_count", 0) for ar in audit_results)
        repo_finding_counts[repo] = total_findings

        for ar in audit_results:
            for finding in ar.get("findings", []):
                pkg = finding.get("package", "unknown")
                vuln = finding.get("vulnerability", "unknown")
                severity = finding.get("severity", "unknown")
                vuln_by_package[pkg].append((repo, vuln, severity))

    # Build summary text
    lines = [
        f"Organization summary: {total_repos} repos scanned",
        f"- PRs created: {len(successful)}",
        f"- Failed/issues: {len(failed)}",
        f"- Already up to date: {len(up_to_date)}",
        "",
    ]

    # Repos with most findings
    if repo_finding_counts:
        lines.append("Findings per repo:")
        for repo, count in sorted(repo_finding_counts.items(), key=lambda x: -x[1]):
            status = next((r.get("status", "") for r in results if r.get("repository") == repo), "")
            lines.append(f"  - {repo}: {count} findings (status: {status})")
        lines.append("")

    # Shared vulnerable packages (affect 2+ repos)
    shared = {pkg: repos for pkg, repos in vuln_by_package.items() if len(set(r[0] for r in repos)) >= 2}
    if shared:
        lines.append("Shared vulnerable packages (across 2+ repos):")
        for pkg, repo_vulns in sorted(shared.items(), key=lambda x: -len(x[1])):
            repos = sorted(set(r[0] for r in repo_vulns))
            vulns = sorted(set(r[1] for r in repo_vulns))[:5]
            lines.append(f"  - {pkg}: affects {len(repos)} repos ({', '.join(repos)})")
            lines.append(f"    CVEs: {', '.join(vulns)}")
        lines.append("")

    # Failed repos
    if failed:
        lines.append("Failed repos (need manual attention):")
        for r in failed:
            lines.append(f"  - {r.get('repository', '?')}: {r.get('message', 'unknown error')[:100]}")

    return "\n".join(lines)


def _build_deterministic_summary(results: list[dict]) -> str:
    """Fallback summary when LLM is unavailable."""
    total = len(results)
    prs = sum(1 for r in results if r.get("status") == "pr_created")
    failed = sum(1 for r in results if r.get("status") in ("error", "issue_created"))
    up_to_date = sum(1 for r in results if r.get("status") == "up_to_date")
    total_findings = sum(
        sum(ar.get("finding_count", 0) for ar in (r.get("audit_results") or []))
        for r in results
    )

    return (
        f"## Multi-Repo Summary\n\n"
        f"Scanned **{total}** repositories: "
        f"{prs} PRs created, {failed} failed, {up_to_date} up to date.\n\n"
        f"Total security findings across all repos: **{total_findings}**\n"
    )
