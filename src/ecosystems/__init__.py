"""
Ecosystem plugin registry.

Each supported language/package manager is a plugin class that knows how to:
- Detect itself from repo files
- Parse its dependency file
- Apply version updates
- Rollback a specific package
- Provide default commands

Adding a new ecosystem = one new file that subclasses EcosystemPlugin.
"""

import importlib
import json
import pkgutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Dependency:
    name: str
    current: str
    latest: str


class EcosystemPlugin(ABC):
    """Base class for ecosystem plugins."""

    name: str  # e.g. "npm", "pip", "cargo"
    language: str  # e.g. "nodejs", "python", "rust"
    detect_files: list[str]  # files whose presence indicates this ecosystem
    lock_files: list[str] = []  # lock files that disambiguate between PMs
    dependency_file: str = ""  # primary dependency file (e.g. "package.json")

    @abstractmethod
    def detect(self, repo_files: set[str]) -> bool:
        """Return True if this ecosystem is detected in the repo."""
        ...

    def resolve_dependency_file(self, repo_files: set[str]) -> Optional[str]:
        """
        Determine which dependency file to edit, based on what actually exists in the repo.

        Override in subclasses that support multiple file formats (e.g. pip supports
        requirements.txt, pyproject.toml, setup.cfg).

        Returns:
            Filename to edit, or None if no known file found
        """
        if self.dependency_file and self.dependency_file in repo_files:
            return self.dependency_file
        # Check detect_files as fallback
        for f in self.detect_files:
            if f in repo_files and not f.startswith("*"):
                return f
        return self.dependency_file  # last resort default

    @abstractmethod
    def parse_dependencies(self, content: str) -> list[Dependency]:
        """Parse a dependency file and return list of dependencies with versions."""
        ...

    @abstractmethod
    def apply_updates(self, content: str, updates: list[dict], file_name: str = "") -> tuple[str, list[dict]]:
        """
        Apply version updates to dependency file content.

        Args:
            content: Current file content
            updates: List of {"name": ..., "current": ..., "latest": ...}
            file_name: The actual file being edited (allows format-specific logic)

        Returns:
            (updated_content, applied_updates)
        """
        ...

    @abstractmethod
    def rollback_package(self, content: str, package_name: str, target_version: str, file_name: str = "") -> str:
        """Rollback a specific package to a target version. Returns updated content."""
        ...

    # ── Update strategy ────────────────────────────────────

    @property
    def updates_via_command(self) -> bool:
        """
        If True, updates are applied by running a command (e.g. go get -u, cargo update)
        rather than editing a dependency file. The plugin must implement update_command().
        Default: False (most ecosystems edit a file).
        """
        return False

    def update_command(self, repo_path: str, outdated_packages: list[dict]) -> Optional[str]:
        """
        Return the shell command to update all dependencies.
        Only called when updates_via_command is True.
        """
        return None

    def post_update_command(self) -> Optional[str]:
        """Optional cleanup command after updates (e.g. 'go mod tidy'). Runs after update_command."""
        return None

    def parse_update_diff(self, diff_output: str, outdated_packages: list[dict]) -> list[dict]:
        """
        Parse `git diff <dep_file>` to extract which packages actually changed.
        Used by command-based updaters to determine applied updates.
        Returns list of {"name": ..., "old": ..., "new": ...}.
        Default: returns the outdated_packages list as-is.
        """
        return [
            {"name": p["name"], "old": p.get("current", "?"), "new": p.get("latest", "?")}
            for p in outdated_packages if p.get("latest") and p["latest"] != "N/A"
        ]

    # ── Rollback strategy ────────────────────────────────

    @property
    def rollback_via_command(self) -> bool:
        """If True, rollback uses a shell command rather than file editing."""
        return False

    def rollback_command(self, package_name: str, target_version: str) -> Optional[str]:
        """Return the shell command to rollback a specific package. Only when rollback_via_command is True."""
        return None

    # ── Command fixing ───────────────────────────────────

    def fix_command(self, command: str) -> str:
        """
        Fix a command so it works on the current system.
        E.g. pip → python3 -m pip when 'pip' isn't on PATH.
        Override in subclasses. Default: return as-is.
        """
        return command

    # ── CI pattern matching ──────────────────────────────

    def ci_build_patterns(self) -> list[str]:
        """Regex patterns that identify build commands in CI configs."""
        return []

    def ci_test_patterns(self) -> list[str]:
        """Regex patterns that identify test commands in CI configs."""
        return []

    def ci_install_patterns(self) -> list[str]:
        """Regex patterns that identify install commands in CI configs."""
        return []

    # ── Outdated output parsing ──────────────────────────

    def default_commands(self) -> dict:
        """Return default build/test/install commands for this ecosystem."""
        return {"install": None, "build": None, "test": None, "lint": None}

    def outdated_command(self) -> Optional[str]:
        """Return the command to check for outdated packages, or None."""
        return None

    def outdated_output_format(self) -> str:
        """Return format of outdated command output: text|json_dict|json_array|ndjson"""
        return "text"

    def outdated_field_map(self) -> dict:
        """Return field mapping for structured outdated output."""
        return {}

    def outdated_skip_when(self) -> dict:
        """Return skip conditions for outdated entries."""
        return {}

    def parse_outdated_text(self, stdout: str) -> list[dict]:
        """
        Override to provide custom text-format outdated parsing.
        Default: generic tabular parser.
        """
        return []


# ── Plugin Registry ──────────────────────────────────────────

_registry: list[EcosystemPlugin] = []


def register(plugin_class: type[EcosystemPlugin]) -> type[EcosystemPlugin]:
    """Class decorator to register an ecosystem plugin."""
    _registry.append(plugin_class())
    return plugin_class


def get_all_plugins() -> list[EcosystemPlugin]:
    """Return all registered ecosystem plugins."""
    return list(_registry)


def detect_ecosystem(repo_files: set[str]) -> Optional[EcosystemPlugin]:
    """
    Detect the ecosystem for a repository by checking lock files first,
    then falling back to detection files.

    Args:
        repo_files: Set of filenames in the repository

    Returns:
        The matching EcosystemPlugin, or None
    """
    # Priority 1: Match by lock file (most specific)
    for plugin in _registry:
        for lock in plugin.lock_files:
            if lock in repo_files:
                return plugin

    # Priority 2: Match by detection files
    for plugin in _registry:
        if plugin.detect(repo_files):
            return plugin

    return None


def get_plugin_by_name(name: str) -> Optional[EcosystemPlugin]:
    """Get a specific plugin by package manager name."""
    for plugin in _registry:
        if plugin.name == name:
            return plugin
    return None


# ── Auto-discover and import all ecosystem plugins ───────────

def _load_plugins():
    """Import all modules in src/ecosystems/ to trigger @register decorators."""
    package_dir = Path(__file__).parent
    for _importer, modname, _ispkg in pkgutil.iter_modules([str(package_dir)]):
        if modname.startswith("_"):
            continue
        importlib.import_module(f"src.ecosystems.{modname}")


_load_plugins()
