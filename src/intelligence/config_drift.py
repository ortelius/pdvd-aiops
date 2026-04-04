"""
Configuration drift detection — finds stale configs after dependency updates.

After updating TypeScript from 4.x to 5.x, your tsconfig.json may still target
ES2017. After updating ESLint, rule configs may reference removed rules. This
analyzer reads config files and the major-bumped dependency versions, then asks
the LLM to flag stale settings.

Cost: ~1 LLM call, only when major bumps exist and config files are present.
"""

import os
from typing import Optional

from src.callbacks.cost_tracker import CostTracker
from src.intelligence.base import invoke_llm

# Config files to scan per ecosystem, mapped to the packages they relate to
_CONFIG_FILES = {
    "nodejs": {
        "tsconfig.json": ["typescript"],
        "tsconfig.*.json": ["typescript"],
        ".eslintrc": ["eslint"],
        ".eslintrc.json": ["eslint"],
        ".eslintrc.js": ["eslint"],
        ".eslintrc.cjs": ["eslint"],
        "eslint.config.js": ["eslint"],
        "eslint.config.mjs": ["eslint"],
        ".prettierrc": ["prettier"],
        ".prettierrc.json": ["prettier"],
        "prettier.config.js": ["prettier"],
        "jest.config.js": ["jest"],
        "jest.config.ts": ["jest"],
        "babel.config.js": ["@babel/core"],
        "babel.config.json": ["@babel/core"],
        ".babelrc": ["@babel/core"],
        "webpack.config.js": ["webpack"],
        "vite.config.ts": ["vite"],
        "vite.config.js": ["vite"],
        "next.config.js": ["next"],
        "next.config.mjs": ["next"],
    },
    "python": {
        "pyproject.toml": ["pytest", "black", "ruff", "mypy"],
        "setup.cfg": ["pytest", "mypy", "flake8"],
        ".flake8": ["flake8"],
        "mypy.ini": ["mypy"],
        ".pylintrc": ["pylint"],
        "pytest.ini": ["pytest"],
        "tox.ini": ["tox"],
        "ruff.toml": ["ruff"],
    },
    "rust": {
        "rustfmt.toml": ["rustfmt"],
        "clippy.toml": ["clippy"],
        ".cargo/config.toml": ["cargo"],
    },
    "go": {
        ".golangci.yml": ["golangci-lint"],
        ".golangci.yaml": ["golangci-lint"],
    },
}

# Max chars of config content per file
_MAX_CONFIG_CONTENT = 1500


class ConfigDriftAnalyzer:
    """Detect stale configuration files after major dependency updates."""

    @property
    def name(self) -> str:
        return "config_drift"

    def should_run(self, state: dict) -> bool:
        """Run when there are major bumps and we have repo access."""
        if not state.get("repo_path"):
            return False
        applied = state.get("applied_updates") or []
        return any(_is_major_bump(u) for u in applied)

    def analyze(self, state: dict, tracker: Optional[CostTracker] = None) -> dict:
        """
        Scan config files for settings that may be stale after major updates.

        Returns: {"config_drift_summary": str}
        """
        repo_path = state.get("repo_path", "")
        language = state.get("language", "")
        applied = state.get("applied_updates") or []

        major_updates = [u for u in applied if _is_major_bump(u)]
        major_names = {u["name"].lower() for u in major_updates}

        # Find config files that relate to major-bumped packages
        config_context = _collect_relevant_configs(repo_path, language, major_names)

        if not config_context:
            return {}

        updates_text = "\n".join(
            f"- {u['name']}: {u.get('old', '?')} → {u['new']}"
            for u in major_updates
        )

        prompt = f"""You are reviewing configuration files after major dependency updates to detect
settings that may be stale, deprecated, or suboptimal for the new versions.

Language: {language}

Major version updates:
{updates_text}

Configuration files found:
{config_context}

For each config file, check:
1. Are there settings that reference old/deprecated options for the updated version?
2. Are there new recommended settings or defaults in the new version?
3. Are there version-specific features that should be enabled?
4. Are there config format changes (e.g. ESLint flat config vs legacy)?

Only report actual drift — don't suggest improvements unrelated to the version updates.
If a config looks fine for the new version, say so briefly.
Format as concise markdown (no top-level heading). Keep under 300 words."""

        summary = invoke_llm(prompt, max_tokens=500, tracker=tracker, phase_name=self.name)
        if not summary:
            return {}

        return {"config_drift_summary": summary}


def _is_major_bump(update: dict) -> bool:
    """Check if an update is a major version change."""
    old = update.get("old", "0.0.0").lstrip("^~>=v")
    new = update.get("new", "0.0.0").lstrip("^~>=v")
    try:
        return old.split(".")[0] != new.split(".")[0]
    except (IndexError, ValueError):
        return False


def _collect_relevant_configs(repo_path: str, language: str, major_package_names: set) -> str:
    """Find and read config files that relate to major-bumped packages."""
    config_map = _CONFIG_FILES.get(language, {})
    if not config_map:
        return ""

    sections = []
    for config_file, related_packages in config_map.items():
        # Check if any related package had a major bump
        if not any(pkg.lower() in major_package_names for pkg in related_packages):
            continue

        # Handle glob patterns (tsconfig.*.json)
        if "*" in config_file:
            _scan_glob_configs(repo_path, config_file, related_packages, sections)
        else:
            config_path = os.path.join(repo_path, config_file)
            if os.path.isfile(config_path):
                content = _read_config(config_path)
                if content:
                    related = ", ".join(p for p in related_packages if p.lower() in major_package_names)
                    sections.append(f"### `{config_file}` (related to: {related})\n```\n{content}\n```")

    return "\n\n".join(sections)


def _scan_glob_configs(repo_path: str, pattern: str, related_packages: list, sections: list):
    """Scan for config files matching a glob pattern."""
    import fnmatch
    try:
        for entry in os.listdir(repo_path):
            if fnmatch.fnmatch(entry, pattern):
                config_path = os.path.join(repo_path, entry)
                if os.path.isfile(config_path):
                    content = _read_config(config_path)
                    if content:
                        sections.append(f"### `{entry}` (related to: {', '.join(related_packages)})\n```\n{content}\n```")
    except OSError:
        pass


def _read_config(path: str) -> str:
    """Read a config file, truncating if too large."""
    try:
        with open(path, "r", errors="ignore") as f:
            content = f.read(_MAX_CONFIG_CONTENT + 100)
        if len(content) > _MAX_CONFIG_CONTENT:
            content = content[:_MAX_CONFIG_CONTENT] + "\n... (truncated)"
        return content.strip()
    except Exception:
        return ""
