"""
Ecosystem scenario tests.

Tests ecosystem plugin fundamentals:
1. Detection — correct plugin is selected from repo files
2. resolve_dependency_file — correct file is chosen
3. rollback_package — a single package is rolled back correctly
4. Plugin properties — updates_via_command, fix_command, CI patterns

Outdated detection tests → test_outdated_detection.py
Apply updates tests → test_apply_updates.py
"""

import json
import os
from pathlib import Path

import pytest

from src.ecosystems import detect_ecosystem, get_plugin_by_name, get_all_plugins

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def get_scenario_files(scenario_name: str) -> set[str]:
    scenario_dir = SCENARIOS_DIR / scenario_name
    return {p.name for p in scenario_dir.rglob("*") if p.is_file()}


# ═══════════════════════════════════════════════════════════════
#  1. DETECTION TESTS
# ═══════════════════════════════════════════════════════════════

class TestEcosystemDetection:
    """Verify the correct plugin is detected for each scenario."""

    def test_detect_npm(self):
        plugin = detect_ecosystem(get_scenario_files("npm_project"))
        assert plugin is not None
        assert plugin.name == "npm"
        assert plugin.language == "nodejs"

    def test_detect_yarn(self):
        plugin = detect_ecosystem(get_scenario_files("yarn_project"))
        assert plugin is not None
        assert plugin.name == "yarn"
        assert plugin.language == "nodejs"

    def test_detect_pnpm(self):
        plugin = detect_ecosystem(get_scenario_files("pnpm_project"))
        assert plugin is not None
        assert plugin.name == "pnpm"
        assert plugin.language == "nodejs"

    def test_detect_pip_requirements(self):
        plugin = detect_ecosystem(get_scenario_files("pip_requirements"))
        assert plugin is not None
        assert plugin.name == "pip"
        assert plugin.language == "python"

    def test_detect_pip_pyproject(self):
        plugin = detect_ecosystem(get_scenario_files("pip_pyproject"))
        assert plugin is not None
        assert plugin.name == "pip"
        assert plugin.language == "python"

    def test_detect_poetry(self):
        plugin = detect_ecosystem(get_scenario_files("poetry_project"))
        assert plugin is not None
        assert plugin.name == "poetry"
        assert plugin.language == "python"

    def test_detect_cargo(self):
        plugin = detect_ecosystem(get_scenario_files("cargo_project"))
        assert plugin is not None
        assert plugin.name == "cargo"
        assert plugin.language == "rust"

    def test_detect_go(self):
        plugin = detect_ecosystem(get_scenario_files("go_project"))
        assert plugin is not None
        assert plugin.name == "go-mod"
        assert plugin.language == "go"

    def test_detect_maven(self):
        plugin = detect_ecosystem(get_scenario_files("maven_project"))
        assert plugin is not None
        assert plugin.name == "maven"
        assert plugin.language == "java"

    def test_detect_gradle(self):
        plugin = detect_ecosystem(get_scenario_files("gradle_project"))
        assert plugin is not None
        assert plugin.name == "gradle"
        assert plugin.language == "java"

    def test_detect_bundler(self):
        plugin = detect_ecosystem(get_scenario_files("bundler_project"))
        assert plugin is not None
        assert plugin.name == "bundler"
        assert plugin.language == "ruby"

    def test_detect_composer(self):
        plugin = detect_ecosystem(get_scenario_files("composer_project"))
        assert plugin is not None
        assert plugin.name == "composer"
        assert plugin.language == "php"

    def test_detect_unknown_returns_none(self):
        plugin = detect_ecosystem({"README.md", "LICENSE", "main.c"})
        assert plugin is None


# ═══════════════════════════════════════════════════════════════
#  2. RESOLVE DEPENDENCY FILE TESTS
# ═══════════════════════════════════════════════════════════════

class TestResolveDependencyFile:
    """Verify each plugin resolves to the correct dependency file."""

    def test_npm_resolves_package_json(self):
        plugin = get_plugin_by_name("npm")
        result = plugin.resolve_dependency_file({"package.json", "package-lock.json", "src"})
        assert result == "package.json"

    def test_pip_resolves_requirements_txt_when_present(self):
        plugin = get_plugin_by_name("pip")
        result = plugin.resolve_dependency_file({"requirements.txt", "main.py"})
        assert result == "requirements.txt"

    def test_pip_resolves_pyproject_when_no_requirements(self):
        plugin = get_plugin_by_name("pip")
        result = plugin.resolve_dependency_file({"pyproject.toml", "src"})
        assert result == "pyproject.toml"

    def test_pip_prefers_requirements_over_pyproject(self):
        plugin = get_plugin_by_name("pip")
        result = plugin.resolve_dependency_file({"requirements.txt", "pyproject.toml"})
        assert result == "requirements.txt"

    def test_poetry_resolves_pyproject(self):
        plugin = get_plugin_by_name("poetry")
        result = plugin.resolve_dependency_file({"pyproject.toml", "poetry.lock"})
        assert result == "pyproject.toml"

    def test_cargo_resolves_cargo_toml(self):
        plugin = get_plugin_by_name("cargo")
        result = plugin.resolve_dependency_file({"Cargo.toml", "Cargo.lock", "src"})
        assert result == "Cargo.toml"

    def test_go_resolves_go_mod(self):
        plugin = get_plugin_by_name("go-mod")
        result = plugin.resolve_dependency_file({"go.mod", "go.sum", "main.go"})
        assert result == "go.mod"


# ═══════════════════════════════════════════════════════════════
#  3. ROLLBACK TESTS
# ═══════════════════════════════════════════════════════════════

class TestRollbackPackage:
    """Verify single-package rollback works correctly."""

    def test_npm_rollback(self):
        plugin = get_plugin_by_name("npm")
        content = json.dumps({
            "dependencies": {"express": "^5.0.0", "lodash": "~4.18.0"}
        }, indent=2)

        rolled_back = plugin.rollback_package(content, "express", "4.18.2", "package.json")
        data = json.loads(rolled_back)

        assert data["dependencies"]["express"] == "^4.18.2"
        assert data["dependencies"]["lodash"] == "~4.18.0"

    def test_pip_rollback_requirements(self):
        plugin = get_plugin_by_name("pip")
        content = "flask==3.0.0\nrequests==2.32.0\n"

        rolled_back = plugin.rollback_package(content, "flask", "2.3.2", "requirements.txt")

        assert "flask==2.3.2" in rolled_back
        assert "requests==2.32.0" in rolled_back

    def test_pip_rollback_pyproject(self):
        plugin = get_plugin_by_name("pip")
        content = 'dependencies = [\n    "fastapi>=0.115.0",\n    "pydantic>=2.5.0",\n]\n'

        rolled_back = plugin.rollback_package(content, "fastapi", "0.104.0", "pyproject.toml")

        assert '"fastapi>=0.104.0"' in rolled_back
        assert '"pydantic>=2.5.0"' in rolled_back

    def test_cargo_rollback(self):
        plugin = get_plugin_by_name("cargo")
        content = '[dependencies]\nserde = "1.0.200"\ntokio = "1.35.0"\n'

        rolled_back = plugin.rollback_package(content, "serde", "1.0.180", "Cargo.toml")

        assert 'serde = "1.0.180"' in rolled_back
        assert 'tokio = "1.35.0"' in rolled_back

    def test_poetry_rollback(self):
        plugin = get_plugin_by_name("poetry")
        content = '[tool.poetry.dependencies]\ndjango = "5.0.0"\nredis = "5.0.0"\n'

        rolled_back = plugin.rollback_package(content, "django", "4.2.0", "pyproject.toml")

        assert 'django = "4.2.0"' in rolled_back
        assert 'redis = "5.0.0"' in rolled_back


# ═══════════════════════════════════════════════════════════════
#  4. PLUGIN PROPERTIES TESTS
# ═══════════════════════════════════════════════════════════════

class TestPluginProperties:
    """Verify plugin metadata and strategy declarations."""

    def test_go_is_command_based(self):
        plugin = get_plugin_by_name("go-mod")
        assert plugin.updates_via_command is True
        assert plugin.rollback_via_command is True
        assert plugin.update_command("/tmp", []) == "go get -u ./..."
        assert "go get" in plugin.rollback_command("github.com/pkg", "v1.0.0")

    def test_cargo_is_command_based(self):
        plugin = get_plugin_by_name("cargo")
        assert plugin.updates_via_command is True
        assert plugin.update_command("/tmp", []) == "cargo update"

    def test_npm_is_file_based(self):
        plugin = get_plugin_by_name("npm")
        assert plugin.updates_via_command is False
        assert plugin.rollback_via_command is False

    def test_pip_is_file_based(self):
        plugin = get_plugin_by_name("pip")
        assert plugin.updates_via_command is False

    def test_pip_fix_command_replaces_pip(self):
        plugin = get_plugin_by_name("pip")
        fixed = plugin.fix_command("pip install -r requirements.txt")
        assert "python" in fixed
        assert "-m pip" in fixed
        assert "install -r requirements.txt" in fixed

    def test_pip_fix_command_replaces_pytest(self):
        plugin = get_plugin_by_name("pip")
        fixed = plugin.fix_command("pytest -v --cov")
        assert "python" in fixed
        assert "-m pytest" in fixed

    def test_pip_fix_command_passthrough(self):
        plugin = get_plugin_by_name("pip")
        assert plugin.fix_command("go build") == "go build"

    def test_npm_fix_command_passthrough(self):
        plugin = get_plugin_by_name("npm")
        assert plugin.fix_command("npm test") == "npm test"

    def test_all_plugins_have_default_commands(self):
        for plugin in get_all_plugins():
            cmds = plugin.default_commands()
            assert isinstance(cmds, dict), f"{plugin.name} default_commands not a dict"
            assert "install" in cmds or "build" in cmds, f"{plugin.name} has no install or build"

    def test_all_plugins_have_outdated_command(self):
        for plugin in get_all_plugins():
            cmd = plugin.outdated_command()
            if cmd is not None:
                assert isinstance(cmd, str), f"{plugin.name} outdated_command not a string"

    def test_ci_patterns_are_lists(self):
        for plugin in get_all_plugins():
            assert isinstance(plugin.ci_build_patterns(), list), f"{plugin.name}"
            assert isinstance(plugin.ci_test_patterns(), list), f"{plugin.name}"
            assert isinstance(plugin.ci_install_patterns(), list), f"{plugin.name}"
