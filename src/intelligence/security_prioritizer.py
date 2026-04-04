"""
Security fix prioritization — Feature #2.

The pipeline already finds vulnerabilities via audit tools. This analyzer reads
the vulnerability descriptions + package usage patterns and produces a prioritized
"what to fix first and why" narrative. Transforms a flat table into actionable
triage guidance.

Cost: ~1 LLM call per run, only when audit findings exist.
"""

from typing import Optional

from src.callbacks.cost_tracker import CostTracker
from src.intelligence.base import invoke_llm


class SecurityPrioritizationAnalyzer:
    """Prioritize security findings by reachability and severity."""

    @property
    def name(self) -> str:
        return "security_prioritization"

    def should_run(self, state: dict) -> bool:
        """Run only when audit results contain findings."""
        audit_results = state.get("audit_results") or []
        return any(r.get("finding_count", 0) > 0 for r in audit_results)

    def analyze(self, state: dict, tracker: Optional[CostTracker] = None) -> dict:
        """
        Produce a prioritized security fix recommendation.

        Returns: {"security_priority_summary": str}
        """
        audit_results = state.get("audit_results") or []
        package_manager = state.get("package_manager", "")
        applied_updates = state.get("applied_updates") or []
        security_fixes = state.get("security_fixes_applied") or []
        unfixable_cves = state.get("unfixable_cves") or []

        # Flatten all findings
        all_findings = []
        for result in audit_results:
            for finding in result.get("findings", []):
                finding_copy = dict(finding)
                finding_copy["_source"] = result.get("source", "")
                all_findings.append(finding_copy)

        if not all_findings:
            return {}

        # Build context
        findings_text = _format_findings_for_llm(all_findings[:30])  # cap to avoid token bloat
        fixes_text = _format_fixes(security_fixes)
        unfixable_text = _format_unfixable(unfixable_cves)
        updated_pkgs = ", ".join(u["name"] for u in applied_updates[:20])

        prompt = f"""You are a security engineer triaging dependency vulnerabilities.
Analyze these audit findings and write a prioritized action plan.

Package manager: {package_manager}
Packages updated in this PR: {updated_pkgs or "none"}

Security fixes already applied: {fixes_text or "none"}
Unfixable CVEs (no fix available): {unfixable_text or "none"}

Audit findings ({len(all_findings)} total):
{findings_text}

Write a prioritized recommendation (most urgent first) that explains:
1. Which vulnerabilities to address first and WHY (reachability, severity, exploit likelihood)
2. For each priority item: the specific package, CVE, and recommended action
3. Which findings can be safely deprioritized and why

Group into: "Fix immediately", "Fix soon", and "Monitor" categories.
Be specific about package names and CVE IDs. Keep under 400 words.
Format as concise markdown (no top-level heading)."""

        summary = invoke_llm(prompt, max_tokens=600, tracker=tracker, phase_name=self.name)
        if not summary:
            return {}

        return {"security_priority_summary": summary}


def _format_findings_for_llm(findings: list[dict]) -> str:
    """Format audit findings into a concise text block for the LLM."""
    lines = []
    for f in findings:
        vuln = f.get("vulnerability", "unknown")
        pkg = f.get("package", "unknown")
        severity = f.get("severity", "unknown")
        detail = f.get("detail", "")[:200]
        fix = ", ".join(f.get("fix_versions", [])) or "no fix"
        source = f.get("_source", "")
        lines.append(f"- [{vuln}] {pkg} (severity: {severity}, fix: {fix}, scanner: {source}): {detail}")
    return "\n".join(lines)


def _format_fixes(fixes: list[dict]) -> str:
    """Format already-applied security fixes."""
    if not fixes:
        return ""
    return ", ".join(
        f"{f['name']} {f.get('old', '?')}→{f['new']} ({f.get('vulnerability', '')})"
        for f in fixes
    )


def _format_unfixable(unfixable: list[dict]) -> str:
    """Format unfixable CVEs."""
    if not unfixable:
        return ""
    return ", ".join(
        f"{u.get('vulnerability', '?')} in {u['package']}"
        for u in unfixable[:10]
    )
