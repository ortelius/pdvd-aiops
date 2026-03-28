"""
Apply updates tests.

Verifies that version updates are applied correctly to dependency files
for each ecosystem, using realistic scenario files.
"""

import json
from pathlib import Path

import pytest

from src.ecosystems import get_plugin_by_name

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def read_scenario_file(scenario_name: str, filename: str) -> str:
    return (SCENARIOS_DIR / scenario_name / filename).read_text()


# ═══════════════════════════════════════════════════════════════
#  npm (package.json)
# ═══════════════════════════════════════════════════════════════


class TestNpmApplyUpdates:

    def test_updates_dependencies(self):
        plugin = get_plugin_by_name("npm")
        content = read_scenario_file("npm_project", "package.json")
        updates = [
            {"name": "express", "current": "4.18.2", "latest": "5.0.0"},
            {"name": "jest", "current": "29.5.0", "latest": "30.0.0"},
        ]

        updated, applied = plugin.apply_updates(content, updates, "package.json")
        data = json.loads(updated)

        assert len(applied) == 2
        assert data["dependencies"]["express"] == "^5.0.0"
        assert data["devDependencies"]["jest"] == "^30.0.0"

    def test_preserves_caret_prefix(self):
        plugin = get_plugin_by_name("npm")
        content = read_scenario_file("npm_project", "package.json")
        updates = [{"name": "express", "current": "4.18.2", "latest": "5.0.0"}]

        updated, _ = plugin.apply_updates(content, updates, "package.json")
        data = json.loads(updated)

        assert data["dependencies"]["express"] == "^5.0.0"

    def test_preserves_tilde_prefix(self):
        plugin = get_plugin_by_name("npm")
        content = read_scenario_file("npm_project", "package.json")
        updates = [{"name": "lodash", "current": "4.17.21", "latest": "4.18.0"}]

        updated, _ = plugin.apply_updates(content, updates, "package.json")
        data = json.loads(updated)

        assert data["dependencies"]["lodash"] == "~4.18.0"

    def test_preserves_gte_prefix(self):
        plugin = get_plugin_by_name("npm")
        content = read_scenario_file("npm_project", "package.json")
        updates = [{"name": "axios", "current": "1.4.0", "latest": "1.6.0"}]

        updated, _ = plugin.apply_updates(content, updates, "package.json")
        data = json.loads(updated)

        assert data["dependencies"]["axios"] == ">=1.6.0"

    def test_ignores_unknown_packages(self):
        plugin = get_plugin_by_name("npm")
        content = read_scenario_file("npm_project", "package.json")
        updates = [{"name": "nonexistent-pkg", "current": "1.0.0", "latest": "2.0.0"}]

        updated, applied = plugin.apply_updates(content, updates, "package.json")

        assert len(applied) == 0
        assert json.loads(updated) == json.loads(content)

    def test_updates_across_sections(self):
        plugin = get_plugin_by_name("npm")
        content = read_scenario_file("npm_project", "package.json")
        updates = [
            {"name": "express", "current": "4.18.2", "latest": "5.0.0"},
            {"name": "typescript", "current": "5.1.0", "latest": "5.3.0"},
        ]

        _, applied = plugin.apply_updates(content, updates, "package.json")

        sections = {a.get("section") for a in applied}
        assert "dependencies" in sections
        assert "devDependencies" in sections

    def test_empty_updates(self):
        plugin = get_plugin_by_name("npm")
        content = read_scenario_file("npm_project", "package.json")

        updated, applied = plugin.apply_updates(content, [], "package.json")

        assert len(applied) == 0
        assert json.loads(updated) == json.loads(content)


# ═══════════════════════════════════════════════════════════════
#  yarn
# ═══════════════════════════════════════════════════════════════


class TestYarnApplyUpdates:

    def test_updates_package_json(self):
        plugin = get_plugin_by_name("yarn")
        content = read_scenario_file("yarn_project", "package.json")
        updates = [{"name": "react", "current": "18.2.0", "latest": "19.0.0"}]

        updated, applied = plugin.apply_updates(content, updates, "package.json")
        data = json.loads(updated)

        assert len(applied) == 1
        assert data["dependencies"]["react"] == "^19.0.0"


# ═══════════════════════════════════════════════════════════════
#  pip (requirements.txt)
# ═══════════════════════════════════════════════════════════════


class TestPipRequirementsApplyUpdates:

    def test_updates_pinned_versions(self):
        plugin = get_plugin_by_name("pip")
        content = read_scenario_file("pip_requirements", "requirements.txt")
        updates = [
            {"name": "flask", "current": "2.3.2", "latest": "3.0.0"},
            {"name": "requests", "current": "2.31.0", "latest": "2.32.0"},
            {"name": "pytest", "current": "7.4.0", "latest": "8.0.0"},
        ]

        updated, applied = plugin.apply_updates(content, updates, "requirements.txt")

        assert len(applied) == 3
        assert "flask==3.0.0" in updated
        assert "requests==2.32.0" in updated
        assert "pytest==8.0.0" in updated

    def test_preserves_comments(self):
        plugin = get_plugin_by_name("pip")
        content = read_scenario_file("pip_requirements", "requirements.txt")
        updates = [{"name": "flask", "current": "2.3.2", "latest": "3.0.0"}]

        updated, _ = plugin.apply_updates(content, updates, "requirements.txt")

        assert "# Core dependencies" in updated
        assert "# Dev tools" in updated

    def test_case_insensitive_matching(self):
        plugin = get_plugin_by_name("pip")
        content = "Flask==2.3.2\n"
        updates = [{"name": "flask", "current": "2.3.2", "latest": "3.0.0"}]

        _, applied = plugin.apply_updates(content, updates, "requirements.txt")

        assert len(applied) == 1

    def test_handles_latest_version_key(self):
        """Some outdated outputs use 'latest_version' instead of 'latest'."""
        plugin = get_plugin_by_name("pip")
        content = "flask==2.3.2\n"
        updates = [{"name": "flask", "current": "2.3.2", "latest_version": "3.0.0"}]

        updated, applied = plugin.apply_updates(content, updates, "requirements.txt")

        assert len(applied) == 1
        assert "flask==3.0.0" in updated


# ═══════════════════════════════════════════════════════════════
#  pip (pyproject.toml — PEP 621)
# ═══════════════════════════════════════════════════════════════


class TestPipPyprojectApplyUpdates:

    def test_updates_gte_pins(self):
        plugin = get_plugin_by_name("pip")
        content = read_scenario_file("pip_pyproject", "pyproject.toml")
        updates = [
            {"name": "fastapi", "current": "0.104.0", "latest": "0.115.0"},
            {"name": "pydantic", "current": "2.0.0", "latest": "2.5.0"},
        ]

        updated, applied = plugin.apply_updates(content, updates, "pyproject.toml")

        assert len(applied) == 2
        assert '"fastapi>=0.115.0"' in updated
        assert '"pydantic>=2.5.0"' in updated

    def test_preserves_extras(self):
        plugin = get_plugin_by_name("pip")
        content = read_scenario_file("pip_pyproject", "pyproject.toml")
        updates = [{"name": "uvicorn", "current": "0.24.0", "latest": "0.30.0"}]

        updated, applied = plugin.apply_updates(content, updates, "pyproject.toml")

        assert len(applied) == 1
        assert '"uvicorn[standard]>=0.30.0"' in updated

    def test_preserves_unmatched_packages(self):
        plugin = get_plugin_by_name("pip")
        content = read_scenario_file("pip_pyproject", "pyproject.toml")
        updates = [{"name": "fastapi", "current": "0.104.0", "latest": "0.115.0"}]

        updated, _ = plugin.apply_updates(content, updates, "pyproject.toml")

        assert '"sqlalchemy>=2.0.0"' in updated

    def test_hyphen_underscore_matching(self):
        """Python packages can use hyphens or underscores interchangeably."""
        plugin = get_plugin_by_name("pip")
        content = 'dependencies = [\n    "my-package>=1.0.0",\n]\n'
        updates = [{"name": "my_package", "current": "1.0.0", "latest": "2.0.0"}]

        updated, applied = plugin.apply_updates(content, updates, "pyproject.toml")

        assert len(applied) == 1
        assert ">=2.0.0" in updated

    def test_preserves_project_metadata(self):
        plugin = get_plugin_by_name("pip")
        content = read_scenario_file("pip_pyproject", "pyproject.toml")
        updates = [{"name": "fastapi", "current": "0.104.0", "latest": "0.115.0"}]

        updated, _ = plugin.apply_updates(content, updates, "pyproject.toml")

        assert 'name = "test-pip-pyproject"' in updated
        assert 'version = "1.0.0"' in updated


# ═══════════════════════════════════════════════════════════════
#  poetry (pyproject.toml)
# ═══════════════════════════════════════════════════════════════


class TestPoetryApplyUpdates:

    def test_updates_dependencies(self):
        plugin = get_plugin_by_name("poetry")
        content = read_scenario_file("poetry_project", "pyproject.toml")
        updates = [
            {"name": "django", "current": "4.2.0", "latest": "5.0.0"},
            {"name": "redis", "current": "4.6.0", "latest": "5.0.0"},
        ]

        updated, applied = plugin.apply_updates(content, updates, "pyproject.toml")

        assert len(applied) == 2
        assert 'django = "5.0.0"' in updated
        assert 'redis = "5.0.0"' in updated

    def test_preserves_python_version(self):
        plugin = get_plugin_by_name("poetry")
        content = read_scenario_file("poetry_project", "pyproject.toml")
        updates = [{"name": "django", "current": "4.2.0", "latest": "5.0.0"}]

        updated, _ = plugin.apply_updates(content, updates, "pyproject.toml")

        assert 'python = "^3.9"' in updated

    def test_preserves_build_system(self):
        plugin = get_plugin_by_name("poetry")
        content = read_scenario_file("poetry_project", "pyproject.toml")
        updates = [{"name": "django", "current": "4.2.0", "latest": "5.0.0"}]

        updated, _ = plugin.apply_updates(content, updates, "pyproject.toml")

        assert 'requires = ["poetry-core"]' in updated


# ═══════════════════════════════════════════════════════════════
#  cargo (Cargo.toml)
# ═══════════════════════════════════════════════════════════════


class TestCargoApplyUpdates:

    def test_updates_dependencies(self):
        plugin = get_plugin_by_name("cargo")
        content = read_scenario_file("cargo_project", "Cargo.toml")
        updates = [
            {"name": "serde", "current": "1.0.180", "latest": "1.0.200"},
            {"name": "tokio", "current": "1.29.0", "latest": "1.35.0"},
        ]

        updated, applied = plugin.apply_updates(content, updates, "Cargo.toml")

        assert len(applied) == 2
        assert 'serde = "1.0.200"' in updated
        assert 'tokio = "1.35.0"' in updated

    def test_preserves_package_section(self):
        plugin = get_plugin_by_name("cargo")
        content = read_scenario_file("cargo_project", "Cargo.toml")
        updates = [{"name": "serde", "current": "1.0.180", "latest": "1.0.200"}]

        updated, _ = plugin.apply_updates(content, updates, "Cargo.toml")

        assert 'name = "test-cargo-project"' in updated
        assert 'version = "0.1.0"' in updated

    def test_updates_dev_dependencies(self):
        plugin = get_plugin_by_name("cargo")
        content = read_scenario_file("cargo_project", "Cargo.toml")
        updates = [{"name": "criterion", "current": "0.5.1", "latest": "0.6.0"}]

        updated, applied = plugin.apply_updates(content, updates, "Cargo.toml")

        assert len(applied) == 1
        assert 'criterion = "0.6.0"' in updated


# ═══════════════════════════════════════════════════════════════
#  go (command-based — apply_updates is a no-op)
# ═══════════════════════════════════════════════════════════════


class TestGoApplyUpdates:

    def test_apply_updates_is_noop(self):
        """Go uses go get commands, not file editing."""
        plugin = get_plugin_by_name("go-mod")
        content = read_scenario_file("go_project", "go.mod")
        updates = [{"name": "github.com/gin-gonic/gin", "current": "v1.9.1", "latest": "v1.10.0"}]

        updated, applied = plugin.apply_updates(content, updates, "go.mod")

        assert updated == content
        assert len(applied) == 0

    def test_update_command_is_go_get(self):
        """Go plugin declares command-based updates."""
        plugin = get_plugin_by_name("go-mod")

        assert plugin.updates_via_command is True
        assert plugin.update_command("/tmp", []) == "go get -u ./..."
        assert plugin.post_update_command() == "go mod tidy"
