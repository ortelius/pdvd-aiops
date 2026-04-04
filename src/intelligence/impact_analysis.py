"""
Application code impact analysis �� scans source code for usage of updated packages.

The pipeline currently never reads application source code. This analyzer greps
the repo for imports/requires of packages that received major version bumps,
collects the call sites, and has the LLM predict which API changes will break
the code — BEFORE the maintainer has to figure it out from a failed build.

Cost: ~1 LLM call, only when major bumps exist and source code imports them.
"""

import os
import re
import subprocess
from typing import Optional

from src.callbacks.cost_tracker import CostTracker
from src.intelligence.base import invoke_llm


# File extensions to scan per language
_SOURCE_EXTENSIONS = {
    "python": ("*.py",),
    "nodejs": ("*.js", "*.ts", "*.jsx", "*.tsx", "*.mjs", "*.cjs"),
    "go": ("*.go",),
    "rust": ("*.rs",),
}

# Max lines of grep output to feed to the LLM per package
_MAX_GREP_LINES_PER_PKG = 15
# Max total source context characters
_MAX_SOURCE_CONTEXT = 3000


class CodeImpactAnalyzer:
    """Scan source code for usage of major-bumped packages and predict breakage."""

    @property
    def name(self) -> str:
        return "code_impact"

    def should_run(self, state: dict) -> bool:
        """Run when there are major bumps and we have access to the repo."""
        if not state.get("repo_path"):
            return False
        applied = state.get("applied_updates") or []
        return any(_is_major_bump(u) for u in applied)

    def analyze(self, state: dict, tracker: Optional[CostTracker] = None) -> dict:
        """
        Grep source code for imports of major-bumped packages, then ask the LLM
        to predict which API changes will affect this code.

        Returns: {"code_impact_summary": str}
        """
        repo_path = state.get("repo_path", "")
        language = state.get("language", "")
        applied = state.get("applied_updates") or []

        major_updates = [u for u in applied if _is_major_bump(u)]
        if not major_updates:
            return {}

        # Grep source files for imports of each major-bumped package
        import_context = _collect_import_context(repo_path, language, major_updates)

        if not import_context:
            # No source code references found — nothing to analyze
            return {}

        updates_text = "\n".join(
            f"- {u['name']}: {u.get('old', '?')} → {u['new']}"
            for u in major_updates
        )

        prompt = f"""You are analyzing application source code to predict the impact of major dependency updates.

Language: {language}

Major version updates applied:
{updates_text}

Source code references found (imports and usage of these packages):
{import_context}

For each package that is imported/used in the source code:
1. Based on the version jump, what API changes likely occurred? (renamed functions,
   removed exports, changed signatures, new required parameters, etc.)
2. Which specific lines in the source code will likely break or need updating?
3. What is the exact code change needed? (old API → new API)

If a package is imported but only uses stable APIs unlikely to change, say so.
Be specific — reference actual function names and file paths from the source context.
Format as concise markdown (no top-level heading). Keep under 400 words."""

        summary = invoke_llm(prompt, max_tokens=600, tracker=tracker, phase_name=self.name)
        if not summary:
            return {}

        return {"code_impact_summary": summary}


def _is_major_bump(update: dict) -> bool:
    """Check if an update is a major version change."""
    old = update.get("old", "0.0.0").lstrip("^~>=v")
    new = update.get("new", "0.0.0").lstrip("^~>=v")
    try:
        return old.split(".")[0] != new.split(".")[0]
    except (IndexError, ValueError):
        return False


def _collect_import_context(repo_path: str, language: str, major_updates: list[dict]) -> str:
    """Grep source files for imports/usage of major-bumped packages."""
    extensions = _SOURCE_EXTENSIONS.get(language, ("*.py", "*.js", "*.go", "*.rs"))
    results = []

    for update in major_updates:
        pkg_name = update["name"]
        # Build grep patterns for this package
        patterns = _import_patterns_for(pkg_name, language)

        matches = []
        for pattern in patterns:
            for ext in extensions:
                lines = _grep_repo(repo_path, pattern, ext)
                matches.extend(lines)

        if matches:
            # Deduplicate and limit
            seen = set()
            unique = []
            for line in matches:
                if line not in seen:
                    seen.add(line)
                    unique.append(line)
            unique = unique[:_MAX_GREP_LINES_PER_PKG]

            results.append(f"### `{pkg_name}` ({update.get('old', '?')} → {update['new']})")
            results.append("\n".join(unique))

    context = "\n\n".join(results)
    # Truncate to avoid token bloat
    if len(context) > _MAX_SOURCE_CONTEXT:
        context = context[:_MAX_SOURCE_CONTEXT] + "\n... (truncated)"
    return context


def _import_patterns_for(package_name: str, language: str) -> list[str]:
    """Generate regex patterns to find imports of a package in source code."""
    # Normalize: @scope/pkg → scope/pkg for matching
    clean = package_name.lstrip("@").replace("/", r"[/\\]")
    base = package_name.split("/")[-1]  # e.g. "go-git" from "github.com/go-git/go-git"

    patterns = [re.escape(package_name)]

    if language == "python":
        # import pkg, from pkg import ..., from pkg.sub import ...
        patterns.append(rf"(?:from|import)\s+{re.escape(base)}")
    elif language == "nodejs":
        # require('pkg'), import ... from 'pkg', import 'pkg'
        patterns.append(rf"""(?:require|from)\s*[\('"]+{re.escape(package_name)}""")
    elif language == "go":
        # "github.com/org/pkg" in import block
        patterns.append(rf'"{re.escape(package_name)}')
        if "/" in package_name:
            # Match the meaningful module name (skip version suffixes like /v5)
            parts = package_name.split("/")
            module_name = next((p for p in reversed(parts) if not re.match(r"^v\d+$", p)), parts[-1])
            patterns.append(rf"\b{re.escape(module_name)}\.")
    elif language == "rust":
        # use pkg::, extern crate pkg
        crate_name = base.replace("-", "_")
        patterns.append(rf"(?:use|extern\s+crate)\s+{re.escape(crate_name)}")

    return patterns


def _grep_repo(repo_path: str, pattern: str, glob: str) -> list[str]:
    """Run ripgrep/grep on the repo, return matching lines with file:line prefix."""
    try:
        # Try ripgrep first (faster)
        result = subprocess.run(
            ["rg", "-n", "--glob", glob, "--no-heading", "-m", "20", pattern, repo_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode <= 1:  # 0=matches, 1=no matches
            lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
            # Strip repo_path prefix for readability
            return [l.replace(repo_path + "/", "") for l in lines]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback to grep
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include", glob, "-m", "20", "-E", pattern, repo_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode <= 1:
            lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
            return [l.replace(repo_path + "/", "") for l in lines]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return []
