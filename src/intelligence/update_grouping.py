"""
Smart update grouping — groups outdated packages into compatible batches.

The prepare node currently applies ALL updates at once. If related packages
(e.g. eslint + eslint-config-airbnb, or react + @types/react) aren't updated
together, the build breaks. This module groups updates so coupled packages
move together and independent ones can be staged separately.

Called from prepare_node BEFORE applying updates — not an analyzer (runs too early).

Cost: ~1 LLM call when >5 outdated packages exist and at least one is a major bump.
"""

from typing import Optional

from src.callbacks.cost_tracker import CostTracker
from src.intelligence.base import invoke_llm


def group_updates(
    outdated_packages: list[dict],
    package_manager: str,
    tracker: Optional[CostTracker] = None,
) -> list[list[dict]]:
    """
    Group outdated packages into compatible update batches.

    Returns a list of groups, where each group is a list of package dicts
    that should be updated together. Groups are ordered by risk (safest first).

    Falls back to a single group (all packages) if the LLM is unavailable
    or grouping isn't needed.
    """
    # Fast path: few packages or no major bumps — don't bother grouping
    if len(outdated_packages) <= 3:
        return [outdated_packages]

    has_major = any(_is_major(p) for p in outdated_packages)
    if not has_major and len(outdated_packages) <= 8:
        return [outdated_packages]

    # Try deterministic grouping first
    groups = _deterministic_grouping(outdated_packages, package_manager)
    if groups and len(groups) > 1:
        return groups

    # Use LLM for complex cases
    return _llm_grouping(outdated_packages, package_manager, tracker)


def _deterministic_grouping(packages: list[dict], package_manager: str) -> list[list[dict]]:
    """
    Group packages using naming conventions and known coupling patterns.

    Handles common cases:
    - @types/X with X (npm)
    - eslint + eslint-config-* + eslint-plugin-*
    - @babel/* family
    - react + react-dom + @types/react
    - pytest + pytest-* (python)
    """
    from collections import defaultdict

    groups_map = defaultdict(list)  # group_key → [packages]
    ungrouped = []

    for pkg in packages:
        name = pkg.get("name", "")
        key = _detect_group_key(name, package_manager)
        if key:
            groups_map[key].append(pkg)
        else:
            ungrouped.append(pkg)

    if not groups_map:
        return []

    # Build ordered groups: patch-only groups first, major-bump groups last
    result = []
    for key in sorted(groups_map.keys()):
        result.append(groups_map[key])

    # Add ungrouped as one batch
    if ungrouped:
        result.append(ungrouped)

    # Sort: groups with only patches first, majors last
    result.sort(key=lambda g: max(1 if _is_major(p) else 0 for p in g))

    return result


def _detect_group_key(name: str, package_manager: str) -> str:
    """Detect which coupling group a package belongs to, or empty string."""
    lower = name.lower()

    if package_manager in ("npm", "yarn", "pnpm"):
        # @types/X couples with X
        if lower.startswith("@types/"):
            return lower.replace("@types/", "")
        # eslint family
        if "eslint" in lower:
            return "eslint"
        # babel family
        if lower.startswith("@babel/") or "babel" in lower:
            return "babel"
        # react family
        if lower in ("react", "react-dom", "react-test-renderer", "@types/react", "@types/react-dom"):
            return "react"
        # jest family
        if lower.startswith("jest") or lower.startswith("@jest/") or lower.startswith("ts-jest"):
            return "jest"
        # webpack family
        if "webpack" in lower:
            return "webpack"
        # typescript + ts-related
        if lower in ("typescript", "ts-node", "ts-loader", "tslib"):
            return "typescript"

    elif package_manager in ("pip", "poetry", "pipenv"):
        # pytest family
        if lower.startswith("pytest"):
            return "pytest"
        # django family
        if lower.startswith("django"):
            return "django"
        # flask family
        if lower.startswith("flask"):
            return "flask"
        # sphinx family
        if lower.startswith("sphinx"):
            return "sphinx"
        # typing extensions
        if lower in ("typing-extensions", "mypy", "pyright"):
            return "typing"

    elif package_manager == "go-mod":
        # Go modules: group by org/repo prefix
        parts = name.split("/")
        if len(parts) >= 3:
            return "/".join(parts[:3])  # github.com/org/repo

    elif package_manager == "cargo":
        # Tokio family
        if lower.startswith("tokio"):
            return "tokio"
        # Serde family
        if lower.startswith("serde"):
            return "serde"

    return ""


def _llm_grouping(
    packages: list[dict],
    package_manager: str,
    tracker: Optional[CostTracker] = None,
) -> list[list[dict]]:
    """
    Use the LLM to group packages when deterministic heuristics aren't sufficient.

    Falls back to single-group (all at once) on failure.
    """
    pkg_list = "\n".join(
        f"- {p['name']}: {p.get('current', '?')} → {p.get('latest', '?')}"
        for p in packages
    )

    prompt = f"""You are grouping dependency updates for a {package_manager} project into
compatible batches that should be applied and tested together.

Outdated packages:
{pkg_list}

Group these packages into update batches following these rules:
1. Packages that are tightly coupled MUST be in the same group (e.g. react + react-dom,
   eslint + eslint plugins, @types/X + X)
2. Independent packages CAN be separate groups
3. Order groups by risk: safest patches first, risky major bumps last
4. Each group should be independently testable after application

Return ONLY a JSON array of arrays of package names:
[["pkg-a", "pkg-b"], ["pkg-c"], ["pkg-d", "pkg-e"]]
No explanation. No markdown. Just the JSON."""

    import json

    response = invoke_llm(prompt, max_tokens=400, tracker=tracker, phase_name="update_grouping")
    if not response:
        return [packages]

    try:
        # Parse JSON response
        content = response.strip()
        if "```" in content:
            import re
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                content = match.group(0)
        groups_raw = json.loads(content)

        if not isinstance(groups_raw, list) or not groups_raw:
            return [packages]

        # Map names back to original package dicts
        pkg_by_name = {p["name"]: p for p in packages}
        groups = []
        used_names = set()

        for group_names in groups_raw:
            if not isinstance(group_names, list):
                continue
            group = []
            for name in group_names:
                if name in pkg_by_name and name not in used_names:
                    group.append(pkg_by_name[name])
                    used_names.add(name)
            if group:
                groups.append(group)

        # Add any packages the LLM missed
        missed = [p for p in packages if p["name"] not in used_names]
        if missed:
            groups.append(missed)

        return groups if groups else [packages]

    except (json.JSONDecodeError, KeyError, TypeError):
        return [packages]


def _is_major(pkg: dict) -> bool:
    """Check if a package update is a major version bump."""
    old = pkg.get("current", "0.0.0").lstrip("^~>=v")
    new = pkg.get("latest", "0.0.0").lstrip("^~>=v")
    try:
        return old.split(".")[0] != new.split(".")[0]
    except (IndexError, ValueError):
        return False
