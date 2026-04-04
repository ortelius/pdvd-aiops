"""
Changelog-aware risk assessment — Feature #1.

For major version bumps, fetches changelog/release notes via the ecosystem
plugin's release_url() and has the LLM summarize breaking changes and
migration steps. Adds actionable context to the PR body that deterministic
tables can't provide.

Cost: ~1 LLM call per run, only when major bumps exist.
"""

from typing import Optional

from src.callbacks.cost_tracker import CostTracker
from src.intelligence.base import invoke_llm


class ChangelogRiskAnalyzer:
    """Analyze changelogs for major version bumps and summarize breaking changes."""

    @property
    def name(self) -> str:
        return "changelog_risk"

    def should_run(self, state: dict) -> bool:
        """Run only when there are major version bumps in applied updates."""
        applied = state.get("applied_updates") or []
        return any(_is_major_bump(u) for u in applied)

    def analyze(self, state: dict, tracker: Optional[CostTracker] = None) -> dict:
        """
        Fetch changelog context for major bumps and produce an LLM risk summary.

        Returns: {"changelog_risk_summary": str}
        """
        applied = state.get("applied_updates") or []
        package_manager = state.get("package_manager", "")

        major_updates = [u for u in applied if _is_major_bump(u)]
        if not major_updates:
            return {}

        # Build changelog context from release URLs
        changelog_context = _build_changelog_context(major_updates, package_manager)

        prompt = f"""You are a dependency update risk analyst. Analyze these major version bumps
and summarize the breaking changes and migration steps a maintainer needs to know.

Package manager: {package_manager}

Major version updates:
{changelog_context}

For each package with a major bump, provide:
1. What likely broke (based on semver major = breaking API changes)
2. Common migration steps for this kind of update
3. Risk level (high/medium/low) based on how widely the package is used

Be specific and actionable. If you don't have changelog details, infer from the
version jump and package name what typical breaking changes look like.

Format as a concise markdown section (no top-level heading). Use bold for package names.
Keep it under 300 words total."""

        summary = invoke_llm(prompt, max_tokens=500, tracker=tracker, phase_name=self.name)
        if not summary:
            return {}

        return {"changelog_risk_summary": summary}


def _is_major_bump(update: dict) -> bool:
    """Check if an update represents a major version change."""
    old = update.get("old", "0.0.0").lstrip("^~>=v")
    new = update.get("new", "0.0.0").lstrip("^~>=v")
    try:
        return old.split(".")[0] != new.split(".")[0]
    except (IndexError, ValueError):
        return False


def _build_changelog_context(major_updates: list[dict], package_manager: str) -> str:
    """Build a text summary of major updates with release URL hints."""
    from src.ecosystems import get_plugin_by_name

    plugin = get_plugin_by_name(package_manager)
    lines = []

    for u in major_updates:
        name = u["name"]
        old_ver = u.get("old", "?")
        new_ver = u.get("new", "?")
        line = f"- **{name}**: {old_ver} → {new_ver} (MAJOR)"

        if plugin:
            url = plugin.release_url(name, new_ver)
            if url:
                line += f"\n  Release: {url}"

        lines.append(line)

    return "\n".join(lines)
