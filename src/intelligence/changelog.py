"""
Changelog-aware risk assessment — fetches REAL changelog content.

For major version bumps, fetches the actual changelog/release notes from
the package registry or GitHub releases, then has the LLM summarize
breaking changes and migration steps with REAL data — not guesses.

Cost: ~1 LLM call per run + 1-3 HTTP fetches. Only when major bumps exist.
"""

import re
import urllib.request
from typing import Optional

from src.callbacks.cost_tracker import CostTracker
from src.intelligence.base import invoke_llm

# Max chars of changelog content per package
_MAX_CHANGELOG_PER_PKG = 2000
# Timeout for HTTP fetches (seconds)
_FETCH_TIMEOUT = 8


class ChangelogRiskAnalyzer:
    """Fetch and analyze changelogs for major version bumps."""

    @property
    def name(self) -> str:
        return "changelog_risk"

    def should_run(self, state: dict) -> bool:
        """Run only when there are major version bumps in applied updates."""
        applied = state.get("applied_updates") or []
        return any(_is_major_bump(u) for u in applied)

    def analyze(self, state: dict, tracker: Optional[CostTracker] = None) -> dict:
        """
        Fetch actual changelog content for major bumps and produce an LLM risk summary.

        Returns: {"changelog_risk_summary": str}
        """
        applied = state.get("applied_updates") or []
        package_manager = state.get("package_manager", "")
        repo_path = state.get("repo_path", "")

        major_updates = [u for u in applied if _is_major_bump(u)]
        if not major_updates:
            return {}

        # Fetch real changelog content
        changelog_context = _build_changelog_context(
            major_updates, package_manager, repo_path
        )

        prompt = f"""You are a dependency update risk analyst. Analyze these major version bumps
and summarize the breaking changes and migration steps a maintainer needs to know.

Package manager: {package_manager}

Major version updates with changelog data:
{changelog_context}

For each package with a major bump, provide:
1. Specific breaking changes (renamed APIs, removed functions, changed defaults)
2. Required migration steps based on the changelog
3. Risk level (high/medium/low) based on scope of changes

Be specific and actionable — reference actual function/API names from the changelog.
If changelog content was not available, note that and infer from the version jump.

Format as concise markdown (no top-level heading). Use bold for package names.
Keep under 400 words total."""

        summary = invoke_llm(prompt, max_tokens=600, tracker=tracker, phase_name=self.name)
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


def _build_changelog_context(
    major_updates: list[dict], package_manager: str, repo_path: str = ""
) -> str:
    """Build changelog context by fetching actual release notes."""
    from src.ecosystems import get_plugin_by_name

    plugin = get_plugin_by_name(package_manager)
    sections = []

    for u in major_updates[:5]:  # cap at 5 to avoid too many fetches
        name = u["name"]
        old_ver = u.get("old", "?")
        new_ver = u.get("new", "?")
        section = f"### **{name}**: {old_ver} → {new_ver} (MAJOR)\n"

        changelog_content = None

        # Strategy 1: Fetch from GitHub releases (most reliable for breaking changes)
        if plugin:
            release_url = plugin.release_url(name, new_ver)
            if release_url:
                changelog_content = _fetch_github_release(name, new_ver, release_url)
                if changelog_content:
                    section += f"**Source**: GitHub release\n"

        # Strategy 2: Fetch from package registry API
        if not changelog_content:
            changelog_content = _fetch_registry_changelog(name, new_ver, package_manager)
            if changelog_content:
                section += f"**Source**: Package registry\n"

        # Strategy 3: Look for CHANGELOG.md in the repo itself (if it's a monorepo dep)
        if not changelog_content and repo_path:
            changelog_content = _read_local_changelog(repo_path, name)
            if changelog_content:
                section += f"**Source**: Local CHANGELOG.md\n"

        if changelog_content:
            # Truncate to avoid token bloat
            if len(changelog_content) > _MAX_CHANGELOG_PER_PKG:
                changelog_content = changelog_content[:_MAX_CHANGELOG_PER_PKG] + "\n... (truncated)"
            section += f"\n```\n{changelog_content}\n```\n"
        else:
            section += "(No changelog content available — infer from version jump)\n"
            if plugin:
                url = plugin.release_url(name, new_ver)
                if url:
                    section += f"Release page: {url}\n"

        sections.append(section)

    return "\n".join(sections)


def _fetch_github_release(package_name: str, version: str, release_url: str) -> str:
    """Fetch release notes from GitHub releases API."""
    # Try to extract owner/repo from the release URL
    match = re.search(r'github\.com/([^/]+/[^/]+)', release_url)
    if not match:
        return ""

    repo = match.group(1)
    # Try common tag formats
    tag_candidates = [f"v{version}", version]

    for tag in tag_candidates:
        api_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
        try:
            req = urllib.request.Request(api_url, headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "pdvd-aiops",
            })
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
                import json
                data = json.loads(resp.read().decode())
                body = data.get("body", "")
                if body:
                    # Strip HTML tags if present
                    body = re.sub(r'<[^>]+>', '', body)
                    return body.strip()
        except Exception:
            continue

    return ""


def _fetch_registry_changelog(package_name: str, version: str, package_manager: str) -> str:
    """Fetch changelog from the package registry API."""
    try:
        if package_manager in ("npm", "yarn", "pnpm"):
            # npm registry sometimes has release info
            url = f"https://registry.npmjs.org/{package_name}/{version}"
            req = urllib.request.Request(url, headers={"User-Agent": "pdvd-aiops"})
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
                import json
                data = json.loads(resp.read().decode())
                # npm doesn't have changelog in registry, but has repository info
                # which we could use to find CHANGELOG.md — skip for now
                return ""

        elif package_manager in ("pip", "poetry", "pipenv"):
            # PyPI has project description which sometimes contains changelog
            url = f"https://pypi.org/pypi/{package_name}/{version}/json"
            req = urllib.request.Request(url, headers={"User-Agent": "pdvd-aiops"})
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
                import json
                data = json.loads(resp.read().decode())
                description = data.get("info", {}).get("description", "")
                # Look for changelog section in description
                changelog_section = _extract_changelog_section(description)
                if changelog_section:
                    return changelog_section

    except Exception:
        pass

    return ""


def _extract_changelog_section(text: str) -> str:
    """Extract the changelog/breaking changes section from a long text."""
    if not text:
        return ""

    # Look for common changelog headers
    patterns = [
        r"(?:^|\n)#+\s*(?:Change\s*Log|Changelog|Breaking\s*Changes?|What'?s?\s*New|Migration)",
        r"(?:^|\n)(?:Change\s*Log|Changelog|Breaking\s*Changes?|BREAKING)\s*\n",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            start = match.start()
            # Extract until next major heading or end
            rest = text[start:]
            # Find end: next heading of same or higher level, or 2000 chars
            end_match = re.search(r'\n#{1,2}\s+[A-Z]', rest[1:])
            if end_match:
                return rest[:end_match.start() + 1].strip()
            return rest[:_MAX_CHANGELOG_PER_PKG].strip()

    return ""


def _read_local_changelog(repo_path: str, package_name: str) -> str:
    """Check if the repo has a CHANGELOG.md and extract the relevant section."""
    import os

    changelog_names = ["CHANGELOG.md", "CHANGES.md", "HISTORY.md", "changelog.md"]
    for name in changelog_names:
        path = os.path.join(repo_path, name)
        if os.path.isfile(path):
            try:
                with open(path, "r", errors="ignore") as f:
                    content = f.read(10000)  # read first 10KB
                section = _extract_changelog_section(content)
                if section:
                    return section
            except Exception:
                pass

    return ""
