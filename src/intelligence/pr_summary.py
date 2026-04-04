"""
PR review summary for maintainers — Feature #4.

Instead of just listing what changed, produces a maintainer-focused summary:
"This PR updates 2 direct deps and 53 transitive deps. The go-git patch fixes
a known auth regression. No breaking API changes. Security posture improves —
4 circl advisories remain but are unreachable from your code."

Cost: ~1 LLM call per run, replaces the simpler _generate_ai_summary.
"""

from typing import Optional

from src.callbacks.cost_tracker import CostTracker
from src.intelligence.base import invoke_llm


class MaintainerSummaryAnalyzer:
    """Generate a maintainer-focused PR review summary."""

    @property
    def name(self) -> str:
        return "maintainer_summary"

    def should_run(self, state: dict) -> bool:
        """Run when there are applied updates or security fixes to summarize."""
        return bool(state.get("applied_updates") or state.get("security_fixes_applied"))

    def analyze(self, state: dict, tracker: Optional[CostTracker] = None) -> dict:
        """
        Produce a comprehensive maintainer-focused PR summary.

        Returns: {"maintainer_summary": str}
        """
        applied_updates = state.get("applied_updates") or []
        security_fixes = state.get("security_fixes_applied") or []
        unfixable_cves = state.get("unfixable_cves") or []
        audit_results = state.get("audit_results") or []
        integration_results = state.get("integration_results") or []
        rollback_history = state.get("rollback_history") or []
        package_manager = state.get("package_manager", "")
        has_tests = state.get("has_tests", True)
        build_log = state.get("build_log", "")
        test_log = state.get("test_log", "")

        # Classify updates
        direct, transitive, major, minor, patch = _classify_updates(applied_updates)

        # Security context
        total_findings = sum(r.get("finding_count", 0) for r in audit_results)
        fixed_count = len(security_fixes)
        unfixable_count = len(unfixable_cves)

        # Integration context
        integration_summary = _summarize_integrations(integration_results)

        # Build/test context
        build_tested = bool(build_log)
        tests_passed = has_tests and bool(test_log)
        rollbacks = len(rollback_history)

        prompt = f"""You are writing a PR review summary for a maintainer who needs to decide
whether to merge this automated dependency update. Be direct, specific, and honest about risks.

Package manager: {package_manager}

Update breakdown:
- Total packages updated: {len(applied_updates)} ({direct} direct, {transitive} transitive)
- Major bumps: {major} | Minor: {minor} | Patch: {patch}

{_format_major_updates(applied_updates)}

Security posture:
- Total advisories found: {total_findings}
- CVEs fixed in this PR: {fixed_count}
- Unfixable CVEs remaining: {unfixable_count}
{_format_security_fixes(security_fixes)}
{_format_unfixable_brief(unfixable_cves)}

Verification:
- Build tested: {"Yes, passed" if build_tested else "No (security-fix-only PR)"}
- Tests: {"Passed" if tests_passed else "No tests found" if not has_tests else "Not run"}
- Rollbacks during testing: {rollbacks}
{integration_summary}

Write a 4-6 sentence maintainer summary covering:
1. What changed (direct vs transitive, risk level)
2. Security impact (what improved, what remains)
3. Verification status (build, tests, integrations)
4. Merge recommendation with any caveats

Be direct and factual. No markdown headers. No bullet points. Plain paragraph text only.
Mention specific package names for major updates and security fixes."""

        summary = invoke_llm(prompt, max_tokens=400, tracker=tracker, phase_name=self.name)
        if not summary:
            return {}

        return {"maintainer_summary": summary}


def _classify_updates(updates: list[dict]) -> tuple[int, int, int, int, int]:
    """Classify updates by type and version bump. Returns (direct, transitive, major, minor, patch)."""
    direct = transitive = major = minor = patch = 0
    for u in updates:
        if u.get("dep_type") == "transitive":
            transitive += 1
        else:
            direct += 1

        old = u.get("old", "0.0.0").lstrip("^~>=v").split(".")
        new = u.get("new", "0.0.0").lstrip("^~>=v").split(".")
        try:
            if old[0] != new[0]:
                major += 1
            elif len(old) >= 2 and len(new) >= 2 and old[1] != new[1]:
                minor += 1
            else:
                patch += 1
        except (IndexError, ValueError):
            patch += 1
    return direct, transitive, major, minor, patch


def _format_major_updates(updates: list[dict]) -> str:
    """Format major updates for the prompt."""
    majors = [
        u for u in updates
        if u.get("old", "0").lstrip("^~>=v").split(".")[0] != u.get("new", "0").lstrip("^~>=v").split(".")[0]
    ]
    if not majors:
        return ""
    lines = ["Major updates (potential breaking changes):"]
    for u in majors:
        lines.append(f"  - {u['name']}: {u.get('old', '?')} → {u['new']}")
    return "\n".join(lines)


def _format_security_fixes(fixes: list[dict]) -> str:
    """Format security fixes for the prompt."""
    if not fixes:
        return ""
    lines = ["Security fixes applied:"]
    for f in fixes[:10]:
        lines.append(f"  - {f['name']}: {f.get('old', '?')} → {f['new']} (fixes {f.get('vulnerability', '?')})")
    return "\n".join(lines)


def _format_unfixable_brief(unfixable: list[dict]) -> str:
    """Brief summary of unfixable CVEs."""
    if not unfixable:
        return ""
    pkgs = sorted(set(u["package"] for u in unfixable))
    return f"Unfixable CVEs in: {', '.join(pkgs[:10])}"


def _summarize_integrations(results: list[dict]) -> str:
    """Summarize integration check results."""
    if not results:
        return ""
    passed = [r["name"] for r in results if r.get("status") == "pass"]
    failed = [r["name"] for r in results if r.get("status") != "pass"]
    parts = []
    if passed:
        parts.append(f"Integration checks passed: {', '.join(passed)}")
    if failed:
        parts.append(f"Integration checks with issues: {', '.join(failed)}")
    return "\n".join(parts)
