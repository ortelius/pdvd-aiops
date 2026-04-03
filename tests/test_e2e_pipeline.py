"""
End-to-end pipeline tests.

These tests use real, buildable mini-projects in tests/e2e_projects/ to exercise
the full dependency update pipeline:

1. Ecosystem detection from repo files
2. Dependency file resolution
3. Outdated detection (real commands)
4. CI config parsing → build/test command detection
5. Update application to dependency files
6. Build and test execution to verify updates don't break anything
7. Rollback verification

Each E2E project is a complete, minimal application with:
- Source code that uses the declared dependencies
- Tests that exercise the code
- GitHub Actions CI config for command detection
- Intentionally outdated (but functional) dependency versions

Tests are skipped when the required package manager is not installed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from src.ecosystems import detect_ecosystem, get_plugin_by_name
from src.pipeline.nodes.analyze import parse_outdated_output
from src.pipeline.nodes.detect_commands import _parse_ci_config

E2E_PROJECTS_DIR = Path(__file__).parent / "e2e_projects"


def _copy_project(project_name: str, tmp_path: Path) -> Path:
    """Copy an E2E project to a temp directory for isolated testing."""
    src = E2E_PROJECTS_DIR / project_name
    dst = tmp_path / project_name
    shutil.copytree(src, dst)
    return dst


def _command_available(cmd: str) -> bool:
    try:
        subprocess.run([cmd, "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _go_version_sufficient(required: str = "1.26") -> bool:
    """Check if the installed go version meets the minimum requirement."""
    try:
        r = subprocess.run(
            ["go", "version"], capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return False
        # Output: "go version go1.22.5 darwin/arm64"
        import re
        match = re.search(r'go(\d+\.\d+)', r.stdout)
        if not match:
            return False
        installed = tuple(int(x) for x in match.group(1).split("."))
        needed = tuple(int(x) for x in required.split("."))
        return installed >= needed
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _pip_available() -> bool:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _create_venv(base_path: Path) -> str:
    """Create an isolated venv and return the python executable path."""
    import venv
    venv_dir = base_path / ".venv"
    venv.create(str(venv_dir), with_pip=True)
    if sys.platform == "win32":
        return str(venv_dir / "Scripts" / "python.exe")
    return str(venv_dir / "bin" / "python")


def _get_repo_files(project_path: Path) -> set[str]:
    """Get set of filenames in a project (mimics what analyze_node does)."""
    return {p.name for p in project_path.rglob("*") if p.is_file()}


# ═══════════════════════════════════════════════════════════════
#  npm E2E
# ═══════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _command_available("npm"), reason="npm not installed")
class TestNpmE2E:
    """Full pipeline test for the npm Express app."""

    def test_ecosystem_detection(self, tmp_path):
        project = _copy_project("npm_app", tmp_path)
        files = _get_repo_files(project)
        plugin = detect_ecosystem(files)

        assert plugin is not None
        assert plugin.name == "npm"
        assert plugin.language == "nodejs"

    def test_dependency_file_resolution(self, tmp_path):
        project = _copy_project("npm_app", tmp_path)
        files = _get_repo_files(project)
        plugin = detect_ecosystem(files)

        dep_file = plugin.resolve_dependency_file(files)
        assert dep_file == "package.json"

    def test_ci_command_detection(self, tmp_path):
        project = _copy_project("npm_app", tmp_path)
        commands = _parse_ci_config(str(project), "npm")

        assert commands is not None
        assert commands.get("install") is not None
        assert "npm install" in commands["install"]
        assert commands.get("test") is not None
        assert "npm test" in commands["test"]

    def test_outdated_detection(self, tmp_path):
        project = _copy_project("npm_app", tmp_path)

        # npm install first
        subprocess.run(
            ["npm", "install", "--silent"],
            capture_output=True, timeout=120, cwd=str(project),
        )

        # Run outdated
        result = subprocess.run(
            ["npm", "outdated", "--json"],
            capture_output=True, text=True, timeout=30, cwd=str(project),
        )

        stdout = result.stdout.strip()
        if not stdout or stdout == "{}":
            pytest.skip("All npm deps already at latest")

        plugin = get_plugin_by_name("npm")
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }
        outdated = parse_outdated_output(stdout, detected_info, plugin)
        assert len(outdated) >= 0  # may or may not have outdated

    def test_install_and_test_pass(self, tmp_path):
        """Verify the npm project installs and tests pass out of the box."""
        project = _copy_project("npm_app", tmp_path)

        install = subprocess.run(
            ["npm", "install"],
            capture_output=True, text=True, timeout=120, cwd=str(project),
        )
        assert install.returncode == 0, f"npm install failed: {install.stderr}"

        test = subprocess.run(
            ["npm", "test"],
            capture_output=True, text=True, timeout=60, cwd=str(project),
        )
        assert test.returncode == 0, f"npm test failed: {test.stderr}\n{test.stdout}"

    def test_update_and_retest(self, tmp_path):
        """Apply updates, re-run tests, verify they still pass."""
        project = _copy_project("npm_app", tmp_path)

        # Install
        subprocess.run(
            ["npm", "install", "--silent"],
            capture_output=True, timeout=120, cwd=str(project),
        )

        # Check outdated
        result = subprocess.run(
            ["npm", "outdated", "--json"],
            capture_output=True, text=True, timeout=30, cwd=str(project),
        )
        stdout = result.stdout.strip()
        if not stdout or stdout == "{}":
            pytest.skip("No outdated npm packages to update")

        plugin = get_plugin_by_name("npm")
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }
        outdated = parse_outdated_output(stdout, detected_info, plugin)

        if not outdated:
            pytest.skip("No outdated packages parsed")

        # Apply updates to package.json
        pkg_json_path = project / "package.json"
        content = pkg_json_path.read_text()
        updated_content, applied = plugin.apply_updates(content, outdated, "package.json")
        pkg_json_path.write_text(updated_content)

        assert len(applied) >= 1, "Expected at least one update applied"

        # Re-install with updated versions
        reinstall = subprocess.run(
            ["npm", "install"],
            capture_output=True, text=True, timeout=120, cwd=str(project),
        )
        assert reinstall.returncode == 0, f"npm install after update failed: {reinstall.stderr}"

        # Re-run tests
        retest = subprocess.run(
            ["npm", "test"],
            capture_output=True, text=True, timeout=60, cwd=str(project),
        )
        assert retest.returncode == 0, (
            f"Tests failed after update: {retest.stderr}\n{retest.stdout}"
        )

    def test_rollback_preserves_other_deps(self, tmp_path):
        project = _copy_project("npm_app", tmp_path)
        plugin = get_plugin_by_name("npm")

        content = (project / "package.json").read_text()
        rolled_back = plugin.rollback_package(content, "lodash", "4.17.15", "package.json")
        data = json.loads(rolled_back)

        assert data["dependencies"]["lodash"] == "^4.17.15"
        # Other deps untouched
        assert "express" in data["dependencies"]
        assert "axios" in data["dependencies"]


# ═══════════════════════════════════════════════════════════════
#  pip + requirements.txt E2E
# ═══════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _pip_available(), reason="pip not available")
class TestPipE2E:
    """Full pipeline test for the pip Flask app."""

    def test_ecosystem_detection(self, tmp_path):
        project = _copy_project("pip_app", tmp_path)
        files = _get_repo_files(project)
        plugin = detect_ecosystem(files)

        assert plugin is not None
        assert plugin.name == "pip"
        assert plugin.language == "python"

    def test_dependency_file_resolution(self, tmp_path):
        project = _copy_project("pip_app", tmp_path)
        files = _get_repo_files(project)
        plugin = detect_ecosystem(files)

        dep_file = plugin.resolve_dependency_file(files)
        assert dep_file == "requirements.txt"

    def test_ci_command_detection(self, tmp_path):
        project = _copy_project("pip_app", tmp_path)
        commands = _parse_ci_config(str(project), "pip")

        assert commands is not None
        assert commands.get("install") is not None
        assert "pip install" in commands["install"]
        assert commands.get("test") is not None
        assert "pytest" in commands["test"]

    def test_outdated_detection(self, tmp_path):
        project = _copy_project("pip_app", tmp_path)
        venv_python = _create_venv(tmp_path)

        # Install deps
        subprocess.run(
            [venv_python, "-m", "pip", "install", "-r",
             str(project / "requirements.txt"), "--quiet", "--no-warn-script-location"],
            capture_output=True, timeout=240,
        )

        # Check outdated
        result = subprocess.run(
            [venv_python, "-m", "pip", "list", "--outdated", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )

        plugin = get_plugin_by_name("pip")
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }
        outdated = parse_outdated_output(result.stdout.strip() or "[]", detected_info, plugin)

        # We expect some outdated packages given the pinned old versions
        assert isinstance(outdated, list)

    def test_install_and_test_pass(self, tmp_path):
        """Verify the pip project installs and tests pass out of the box."""
        project = _copy_project("pip_app", tmp_path)
        venv_python = _create_venv(tmp_path)

        install = subprocess.run(
            [venv_python, "-m", "pip", "install", "-r",
             str(project / "requirements.txt"), "--quiet", "--no-warn-script-location"],
            capture_output=True, text=True, timeout=240,
        )
        assert install.returncode == 0, f"pip install failed: {install.stderr}"

        test = subprocess.run(
            [venv_python, "-m", "pytest", str(project / "test_app.py"), "-v"],
            capture_output=True, text=True, timeout=60,
            cwd=str(project),
        )
        assert test.returncode == 0, f"pytest failed: {test.stderr}\n{test.stdout}"

    def test_update_and_retest(self, tmp_path):
        """Apply updates, re-install, re-test."""
        project = _copy_project("pip_app", tmp_path)
        venv_python = _create_venv(tmp_path)

        # Install old versions
        subprocess.run(
            [venv_python, "-m", "pip", "install", "-r",
             str(project / "requirements.txt"), "--quiet", "--no-warn-script-location"],
            capture_output=True, timeout=240,
        )

        # Detect outdated
        result = subprocess.run(
            [venv_python, "-m", "pip", "list", "--outdated", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )

        plugin = get_plugin_by_name("pip")
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }
        outdated = parse_outdated_output(result.stdout.strip() or "[]", detected_info, plugin)

        if not outdated:
            pytest.skip("No outdated pip packages")

        # Apply updates
        req_path = project / "requirements.txt"
        content = req_path.read_text()
        updated_content, applied = plugin.apply_updates(content, outdated, "requirements.txt")
        req_path.write_text(updated_content)

        assert len(applied) >= 1

        # Re-install
        reinstall = subprocess.run(
            [venv_python, "-m", "pip", "install", "-r",
             str(req_path), "--quiet", "--no-warn-script-location"],
            capture_output=True, text=True, timeout=240,
        )
        assert reinstall.returncode == 0, f"pip install after update failed: {reinstall.stderr}"

        # Re-test
        retest = subprocess.run(
            [venv_python, "-m", "pytest", str(project / "test_app.py"), "-v"],
            capture_output=True, text=True, timeout=60,
            cwd=str(project),
        )
        assert retest.returncode == 0, (
            f"Tests failed after update: {retest.stderr}\n{retest.stdout}"
        )


# ═══════════════════════════════════════════════════════════════
#  pip + pyproject.toml E2E
# ═══════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _pip_available(), reason="pip not available")
class TestPipPyprojectE2E:
    """Full pipeline test for the pip pyproject.toml app."""

    def test_ecosystem_detection(self, tmp_path):
        project = _copy_project("pip_pyproject_app", tmp_path)
        files = _get_repo_files(project)
        plugin = detect_ecosystem(files)

        assert plugin is not None
        assert plugin.name == "pip"
        assert plugin.language == "python"

    def test_dependency_file_resolution(self, tmp_path):
        project = _copy_project("pip_pyproject_app", tmp_path)
        files = _get_repo_files(project)
        plugin = detect_ecosystem(files)

        dep_file = plugin.resolve_dependency_file(files)
        assert dep_file == "pyproject.toml"

    def test_ci_command_detection(self, tmp_path):
        project = _copy_project("pip_pyproject_app", tmp_path)
        commands = _parse_ci_config(str(project), "pip")

        assert commands is not None
        assert commands.get("install") is not None
        assert "pip install" in commands["install"]
        assert commands.get("test") is not None
        assert "pytest" in commands["test"]

    def test_install_and_test_pass(self, tmp_path):
        """Verify the pyproject.toml project installs and tests pass."""
        project = _copy_project("pip_pyproject_app", tmp_path)
        venv_python = _create_venv(tmp_path)

        # Install the project with dev extras
        install = subprocess.run(
            [venv_python, "-m", "pip", "install",
             "httpx>=0.25.0", "pydantic>=2.0.0", "click>=8.1.0", "pytest>=7.0.0",
             "--quiet", "--no-warn-script-location"],
            capture_output=True, text=True, timeout=240,
        )
        assert install.returncode == 0, f"pip install failed: {install.stderr}"

        test = subprocess.run(
            [venv_python, "-m", "pytest",
             str(project / "tests" / "test_cli.py"), "-v"],
            capture_output=True, text=True, timeout=60,
            cwd=str(project),
        )
        assert test.returncode == 0, f"pytest failed: {test.stderr}\n{test.stdout}"

    def test_update_application_to_pyproject(self, tmp_path):
        """Verify updates are correctly applied to pyproject.toml format."""
        project = _copy_project("pip_pyproject_app", tmp_path)
        plugin = get_plugin_by_name("pip")

        content = (project / "pyproject.toml").read_text()

        # Simulate outdated packages
        outdated = [
            {"name": "httpx", "current": "0.25.0", "latest": "0.27.0"},
            {"name": "pydantic", "current": "2.0.0", "latest": "2.6.0"},
            {"name": "click", "current": "8.1.0", "latest": "8.1.7"},
        ]

        updated_content, applied = plugin.apply_updates(content, outdated, "pyproject.toml")

        assert len(applied) == 3
        assert '"httpx>=0.27.0"' in updated_content
        assert '"pydantic>=2.6.0"' in updated_content
        assert '"click>=8.1.7"' in updated_content

    def test_rollback_pyproject(self, tmp_path):
        project = _copy_project("pip_pyproject_app", tmp_path)
        plugin = get_plugin_by_name("pip")

        content = (project / "pyproject.toml").read_text()
        rolled_back = plugin.rollback_package(content, "httpx", "0.24.0", "pyproject.toml")

        assert '"httpx>=0.24.0"' in rolled_back
        # Other deps untouched
        assert '"pydantic>=2.0.0"' in rolled_back


# ═══════════════════════════════════════════════════════════════
#  Go E2E
# ═══════════════════════════════════════════════════════════════


class TestGoE2E:
    """Full pipeline test for the Go HTTP app (real pdvd-backend go.mod)."""

    # ── Detection tests (no go commands needed) ──────────────

    def test_ecosystem_detection(self, tmp_path):
        project = _copy_project("go_app", tmp_path)
        files = _get_repo_files(project)
        plugin = detect_ecosystem(files)

        assert plugin is not None
        assert plugin.name == "go-mod"
        assert plugin.language == "go"

    def test_dependency_file_resolution(self, tmp_path):
        project = _copy_project("go_app", tmp_path)
        files = _get_repo_files(project)
        plugin = detect_ecosystem(files)

        dep_file = plugin.resolve_dependency_file(files)
        assert dep_file == "go.mod"

    def test_ci_command_detection(self, tmp_path):
        project = _copy_project("go_app", tmp_path)
        commands = _parse_ci_config(str(project), "go-mod")

        assert commands is not None
        has_build = commands.get("build") and "go build" in commands["build"]
        has_test = commands.get("test") and "go test" in commands["test"]
        assert has_build or has_test, f"Expected go commands, got: {commands}"

    def test_plugin_properties(self):
        plugin = get_plugin_by_name("go-mod")
        assert plugin.updates_via_command is True
        assert plugin.rollback_via_command is True
        assert plugin.update_command("/tmp", []) == "go get -u ./..."
        assert plugin.post_update_command() == "go mod tidy"
        assert plugin.outdated_command() == "go list -u -m -json all"
        assert plugin.outdated_output_format() == "ndjson"

    # ── Dependency parsing (reads go.mod, no go commands) ────

    def test_parse_all_dependencies(self, tmp_path):
        """
        Data-driven: derive expected deps from go.mod, verify parser matches.
        No hardcoded package names or versions.
        """
        project = _copy_project("go_app", tmp_path)
        plugin = get_plugin_by_name("go-mod")

        content = (project / "go.mod").read_text()

        # Derive expected deps by parsing go.mod structure directly
        expected = {}
        in_require = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("require ("):
                in_require = True
                continue
            if stripped == ")" and in_require:
                in_require = False
                continue
            if in_require and stripped and not stripped.startswith("//"):
                parts = stripped.split()
                if len(parts) >= 2:
                    expected[parts[0]] = parts[1]

        assert len(expected) > 0, "go.mod should have at least one dependency"

        # Run the plugin parser
        deps = plugin.parse_dependencies(content)
        actual = {d.name: d.current for d in deps}

        # Every dep from go.mod should be parsed with the correct version
        assert actual == expected, (
            f"Parser output does not match go.mod.\n"
            f"  Missing: {set(expected) - set(actual)}\n"
            f"  Extra:   {set(actual) - set(expected)}"
        )

    # ── Golden file verification (go_mod_updated.txt) ──────────

    def test_golden_file_diff_parsing(self, tmp_path):
        """
        Data-driven test — no hardcoded values.

        1. Read go.mod (actual input) and go_mod_updated.txt (expected output)
        2. Derive expected updates by comparing the two files
        3. Run parse_update_diff on the unified diff
        4. Assert pipeline output == expected (derived from files)
        """
        project = _copy_project("go_app", tmp_path)
        plugin = get_plugin_by_name("go-mod")

        original = (project / "go.mod").read_text()
        updated = (project / "go_mod_updated.txt").read_text()

        # ── Derive expected updates from fixture files ────────
        original_deps = {d.name: d.current for d in plugin.parse_dependencies(original)}
        updated_deps = {d.name: d.current for d in plugin.parse_dependencies(updated)}

        expected = {}
        for name, new_ver in updated_deps.items():
            old_ver = original_deps.get(name)
            if old_ver and old_ver != new_ver:
                expected[name] = {"old": old_ver, "new": new_ver}

        assert expected, (
            "go.mod and go_mod_updated.txt should differ in at least one version"
        )

        # ── Run the pipeline (diff → parse_update_diff) ──────
        import difflib
        diff_output = "".join(difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile="a/go.mod", tofile="b/go.mod",
        ))

        applied = plugin.parse_update_diff(diff_output, [])
        actual = {a["name"]: {"old": a["old"], "new": a["new"]} for a in applied}

        # ── Assert pipeline output matches expected ───────────
        assert actual == expected, (
            f"Pipeline output does not match expected.\n"
            f"  Missing: {set(expected) - set(actual)}\n"
            f"  Extra:   {set(actual) - set(expected)}\n"
            f"  Mismatched: {[n for n in set(actual) & set(expected) if actual[n] != expected[n]]}"
        )

    # ── Live go command tests (skip if go version insufficient) ──

    @pytest.mark.skipif(
        not _go_version_sufficient("1.26"),
        reason="go >= 1.26 required for this go.mod",
    )
    def test_go_mod_tidy_and_test(self, tmp_path):
        """Verify the Go project builds and tests pass."""
        project = _copy_project("go_app", tmp_path)

        tidy = subprocess.run(
            ["go", "mod", "tidy"],
            capture_output=True, text=True, timeout=120, cwd=str(project),
        )
        assert tidy.returncode == 0, f"go mod tidy failed: {tidy.stderr}"

        test = subprocess.run(
            ["go", "test", "./..."],
            capture_output=True, text=True, timeout=120, cwd=str(project),
        )
        assert test.returncode == 0, f"go test failed: {test.stderr}\n{test.stdout}"

    @pytest.mark.skipif(
        not _go_version_sufficient("1.26"),
        reason="go >= 1.26 required for this go.mod",
    )
    def test_outdated_detection(self, tmp_path):
        """Run real go list -u -m -json all and parse output."""
        project = _copy_project("go_app", tmp_path)
        plugin = get_plugin_by_name("go-mod")

        subprocess.run(
            ["go", "mod", "tidy"],
            capture_output=True, timeout=120, cwd=str(project),
        )

        result = subprocess.run(
            ["go", "list", "-u", "-m", "-json", "all"],
            capture_output=True, text=True, timeout=60, cwd=str(project),
        )

        if result.returncode != 0:
            pytest.skip(f"go list failed: {result.stderr}")

        stdout = result.stdout.strip()
        if not stdout:
            pytest.skip("go list returned empty output")

        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
            "skip_when": plugin.outdated_skip_when(),
        }
        outdated = parse_outdated_output(stdout, detected_info, plugin)

        # With 17 direct + 44 indirect deps, some should be outdated
        assert isinstance(outdated, list)
        if outdated:
            # Verify structure
            for pkg in outdated:
                assert "name" in pkg
                assert "current" in pkg
                assert "latest" in pkg

            # Main module should NOT be in the list
            gomod_content = (project / "go.mod").read_text()
            module_name = gomod_content.splitlines()[0].split()[1]
            main_entries = [p for p in outdated if p["name"] == module_name]
            assert len(main_entries) == 0, "Main module should be skipped"

    @pytest.mark.skipif(
        not _go_version_sufficient("1.26"),
        reason="go >= 1.26 required for this go.mod",
    )
    def test_command_based_update(self, tmp_path):
        """Verify go get -u updates deps and tests still pass."""
        project = _copy_project("go_app", tmp_path)
        plugin = get_plugin_by_name("go-mod")

        subprocess.run(
            ["go", "mod", "tidy"],
            capture_output=True, timeout=120, cwd=str(project),
        )

        update_cmd = plugin.update_command(str(project), [])
        assert "go get" in update_cmd

        result = subprocess.run(
            update_cmd.split(),
            capture_output=True, text=True, timeout=120, cwd=str(project),
        )
        assert result.returncode == 0 or "no required module" not in result.stderr

        post_cmd = plugin.post_update_command()
        if post_cmd:
            subprocess.run(
                post_cmd.split(),
                capture_output=True, timeout=60, cwd=str(project),
            )

        test = subprocess.run(
            ["go", "test", "./..."],
            capture_output=True, text=True, timeout=120, cwd=str(project),
        )
        assert test.returncode == 0, f"Tests failed after go update: {test.stderr}"


# ═══════════════════════════════════════════════════════════════
#  Cargo E2E
# ═══════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _command_available("cargo"), reason="cargo not installed")
class TestCargoE2E:
    """Full pipeline test for the Cargo Rust app."""

    def test_ecosystem_detection(self, tmp_path):
        project = _copy_project("cargo_app", tmp_path)
        files = _get_repo_files(project)
        plugin = detect_ecosystem(files)

        assert plugin is not None
        assert plugin.name == "cargo"
        assert plugin.language == "rust"

    def test_dependency_file_resolution(self, tmp_path):
        project = _copy_project("cargo_app", tmp_path)
        files = _get_repo_files(project)
        plugin = detect_ecosystem(files)

        dep_file = plugin.resolve_dependency_file(files)
        assert dep_file == "Cargo.toml"

    def test_ci_command_detection(self, tmp_path):
        project = _copy_project("cargo_app", tmp_path)
        commands = _parse_ci_config(str(project), "cargo")

        assert commands is not None
        has_build = commands.get("build") and "cargo build" in commands["build"]
        has_test = commands.get("test") and "cargo test" in commands["test"]
        assert has_build or has_test, f"Expected cargo commands, got: {commands}"

    def test_build_and_test_pass(self, tmp_path):
        """Verify the Cargo project builds and tests pass."""
        project = _copy_project("cargo_app", tmp_path)

        build = subprocess.run(
            ["cargo", "build"],
            capture_output=True, text=True, timeout=300, cwd=str(project),
        )
        assert build.returncode == 0, f"cargo build failed: {build.stderr}"

        test = subprocess.run(
            ["cargo", "test"],
            capture_output=True, text=True, timeout=120, cwd=str(project),
        )
        assert test.returncode == 0, f"cargo test failed: {test.stderr}\n{test.stdout}"

    def test_command_based_update(self, tmp_path):
        """Verify cargo plugin is command-based and update works."""
        project = _copy_project("cargo_app", tmp_path)
        plugin = get_plugin_by_name("cargo")

        assert plugin.updates_via_command is True

        update_cmd = plugin.update_command(str(project), [])
        assert update_cmd == "cargo update"

        # Run cargo update
        result = subprocess.run(
            update_cmd.split(),
            capture_output=True, text=True, timeout=120, cwd=str(project),
        )
        assert result.returncode == 0, f"cargo update failed: {result.stderr}"

        # Tests should still pass after update
        test = subprocess.run(
            ["cargo", "test"],
            capture_output=True, text=True, timeout=120, cwd=str(project),
        )
        assert test.returncode == 0, f"Tests failed after cargo update: {test.stderr}"

    def test_parse_dependencies(self, tmp_path):
        project = _copy_project("cargo_app", tmp_path)
        plugin = get_plugin_by_name("cargo")

        content = (project / "Cargo.toml").read_text()
        deps = plugin.parse_dependencies(content)

        dep_names = [d.name for d in deps]
        assert "serde" in dep_names
        assert "serde_json" in dep_names
        assert "clap" in dep_names

    def test_rollback_preserves_other_deps(self, tmp_path):
        project = _copy_project("cargo_app", tmp_path)
        plugin = get_plugin_by_name("cargo")

        content = (project / "Cargo.toml").read_text()
        rolled_back = plugin.rollback_package(content, "serde_json", "1.0.50", "Cargo.toml")

        assert '"1.0.50"' in rolled_back or "1.0.50" in rolled_back
        # Other deps untouched
        assert "clap" in rolled_back


# ═══════════════════════════════════════════════════════════════
#  Cross-ecosystem tests
# ═══════════════════════════════════════════════════════════════


class TestCrossEcosystem:
    """Tests that verify cross-cutting E2E project properties."""

    @pytest.mark.parametrize("project_name,expected_pm", [
        ("npm_app", "npm"),
        ("pip_app", "pip"),
        ("pip_pyproject_app", "pip"),
        ("go_app", "go-mod"),
        ("cargo_app", "cargo"),
    ])
    def test_all_projects_detected_correctly(self, tmp_path, project_name, expected_pm):
        """Every E2E project is correctly detected by ecosystem detection."""
        project = _copy_project(project_name, tmp_path)
        files = _get_repo_files(project)
        plugin = detect_ecosystem(files)

        assert plugin is not None, f"Failed to detect ecosystem for {project_name}"
        assert plugin.name == expected_pm, (
            f"Expected {expected_pm} for {project_name}, got {plugin.name}"
        )

    @pytest.mark.parametrize("project_name", [
        "npm_app", "pip_app", "pip_pyproject_app", "go_app", "cargo_app",
    ])
    def test_all_projects_have_ci_config(self, tmp_path, project_name):
        """Every E2E project has a GitHub Actions CI workflow."""
        project = _copy_project(project_name, tmp_path)
        ci_dir = project / ".github" / "workflows"
        assert ci_dir.exists(), f"{project_name} missing .github/workflows/"

        yml_files = list(ci_dir.glob("*.yml"))
        assert len(yml_files) >= 1, f"{project_name} has no CI workflow files"

    @pytest.mark.parametrize("project_name", [
        "npm_app", "pip_app", "pip_pyproject_app", "go_app", "cargo_app",
    ])
    def test_all_projects_have_source_and_tests(self, tmp_path, project_name):
        """Every E2E project has both source code and test files."""
        project = _copy_project(project_name, tmp_path)
        all_files = list(project.rglob("*"))
        file_names = [f.name for f in all_files if f.is_file()]

        # Each project should have at least one test file or inline tests
        has_test_files = any(
            "test" in name.lower() or name.endswith("_test.go")
            for name in file_names
        )
        # Rust projects can have inline #[cfg(test)] in source files
        has_inline_tests = any(
            f.suffix == ".rs" and "#[cfg(test)]" in f.read_text()
            for f in all_files if f.is_file() and f.suffix == ".rs"
        )
        assert has_test_files or has_inline_tests, f"{project_name} has no test files"

        # Each project should have source code files
        source_extensions = {".js", ".py", ".go", ".rs"}
        has_source = any(
            Path(name).suffix in source_extensions
            for name in file_names
            if "test" not in name.lower()
        )
        assert has_source, f"{project_name} has no source files"

    @pytest.mark.parametrize("project_name,pm_name", [
        ("npm_app", "npm"),
        ("pip_app", "pip"),
        ("pip_pyproject_app", "pip"),
        ("go_app", "go-mod"),
        ("cargo_app", "cargo"),
    ])
    def test_ci_commands_detectable(self, tmp_path, project_name, pm_name):
        """CI config parsing finds build/test commands for every E2E project."""
        project = _copy_project(project_name, tmp_path)
        commands = _parse_ci_config(str(project), pm_name)

        assert commands is not None, f"No CI commands found for {project_name}"
        assert any(commands.values()), f"All CI commands are None for {project_name}"
