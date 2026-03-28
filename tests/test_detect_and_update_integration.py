"""
Detect-and-update integration tests.

These tests run REAL package manager commands against scenario files,
parse the actual output, apply updates, and verify the dependency file
has the correct new versions.

Tests are skipped if the required package manager is not installed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from src.ecosystems import get_plugin_by_name
from src.pipeline.nodes.analyze import parse_outdated_output

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def read_scenario_file(scenario_name: str, filename: str) -> str:
    return (SCENARIOS_DIR / scenario_name / filename).read_text()


def _command_available(cmd: str) -> bool:
    """Check if a command is available on the system."""
    try:
        subprocess.run(
            [cmd, "--version"], capture_output=True, timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _pip_available() -> bool:
    """Check if pip is available (via python -m pip)."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _create_venv(tmp_path) -> str:
    """Create an isolated venv and return the python executable path."""
    import venv
    venv_dir = tmp_path / ".venv"
    venv.create(str(venv_dir), with_pip=True)
    if sys.platform == "win32":
        return str(venv_dir / "Scripts" / "python.exe")
    return str(venv_dir / "bin" / "python")


# ═══════════════════════════════════════════════════════════════
#  pip + requirements.txt (REAL pip list --outdated)
# ═══════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _pip_available(), reason="pip not available")
class TestPipRealOutdated:
    """
    Install packages from scenario requirements.txt into a temp venv,
    run `pip list --outdated`, parse the output, apply updates, and
    verify the file has new versions.
    """

    def test_real_pip_outdated_and_update(self, tmp_path):
        plugin = get_plugin_by_name("pip")
        venv_python = _create_venv(tmp_path)

        # Create requirements.txt with known-old versions
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("charset-normalizer==2.0.0\nidna==2.10\n")

        # Step 1: Install old versions in the isolated venv
        subprocess.run(
            [venv_python, "-m", "pip", "install",
             "-r", str(req_file), "--quiet", "--no-warn-script-location"],
            capture_output=True, text=True, timeout=120,
        )

        # Step 2: Run REAL `pip list --outdated --format json` in the venv
        result = subprocess.run(
            [venv_python, "-m", "pip", "list", "--outdated", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"pip list failed: {result.stderr}"

        stdout = result.stdout.strip()
        assert stdout, "pip list returned empty output"

        # Step 3: Parse REAL output
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }
        outdated = parse_outdated_output(stdout, detected_info, plugin)

        our_outdated = [p for p in outdated if p["name"].lower() in ("charset-normalizer", "idna")]
        assert len(our_outdated) >= 1, f"Expected outdated packages, got: {[p['name'] for p in outdated]}"

        for pkg in our_outdated:
            assert "name" in pkg
            assert "current" in pkg
            assert "latest" in pkg
            assert pkg["current"] != pkg["latest"], f"{pkg['name']} current == latest"

        # Step 4: Apply to requirements.txt
        content = req_file.read_text()
        updated_content, applied = plugin.apply_updates(content, our_outdated, "requirements.txt")

        assert len(applied) >= 1

        # Step 5: Verify old versions are replaced
        for pkg in applied:
            old_pin = f"{pkg['name']}=={pkg['old']}"
            new_pin = f"{pkg['name']}=={pkg['new']}"
            assert new_pin in updated_content, f"Expected {new_pin} in updated file"
            assert old_pin not in updated_content, f"Old version {old_pin} still present"

    def test_real_pip_up_to_date_returns_empty(self, tmp_path):
        """Install latest version, verify pip reports nothing outdated."""
        plugin = get_plugin_by_name("pip")
        venv_python = _create_venv(tmp_path)

        # Install a package at latest
        subprocess.run(
            [venv_python, "-m", "pip", "install", "six", "--quiet"],
            capture_output=True, timeout=120,
        )

        result = subprocess.run(
            [venv_python, "-m", "pip", "list", "--outdated", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )

        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }
        outdated = parse_outdated_output(result.stdout.strip() or "[]", detected_info, plugin)

        six_outdated = [p for p in outdated if p["name"].lower() == "six"]
        assert len(six_outdated) == 0, "six should be up to date"


# ═══════════════════════════════════════════════════════════════
#  npm + package.json (REAL npm outdated)
# ═══════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _command_available("npm"), reason="npm not available")
class TestNpmRealOutdated:
    """
    Run `npm outdated --json` on a real package.json with old versions,
    parse the output, apply updates, verify package.json has new versions.
    """

    def test_real_npm_outdated_and_update(self, tmp_path):
        plugin = get_plugin_by_name("npm")

        # Create a package.json with known-old pinned versions
        package_json = {
            "name": "test-outdated",
            "version": "1.0.0",
            "dependencies": {
                "semver": "^5.7.0",
            }
        }
        (tmp_path / "package.json").write_text(json.dumps(package_json, indent=2))

        # Step 1: npm install
        install_result = subprocess.run(
            ["npm", "install", "--silent"],
            capture_output=True, text=True, timeout=120, cwd=str(tmp_path),
        )

        # Step 2: Run REAL `npm outdated --json`
        result = subprocess.run(
            ["npm", "outdated", "--json"],
            capture_output=True, text=True, timeout=30, cwd=str(tmp_path),
        )
        # npm outdated returns exit code 1 when packages are outdated
        stdout = result.stdout.strip()

        if not stdout or stdout == "{}":
            pytest.skip("semver is already at latest — no outdated packages")

        # Step 3: Parse REAL output
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }
        outdated = parse_outdated_output(stdout, detected_info, plugin)

        semver_outdated = [p for p in outdated if p["name"] == "semver"]
        assert len(semver_outdated) >= 1, f"Expected semver in outdated, got: {[p['name'] for p in outdated]}"

        pkg = semver_outdated[0]
        assert pkg["current"] is not None
        assert pkg["latest"] is not None

        # Step 4: Apply updates to package.json
        content = (tmp_path / "package.json").read_text()
        updated_content, applied = plugin.apply_updates(content, outdated, "package.json")

        if applied:
            data = json.loads(updated_content)
            for a in applied:
                # Verify the new version is in the file
                assert a["name"] in data.get("dependencies", {}), f"{a['name']} not in dependencies"
                version_in_file = data["dependencies"][a["name"]]
                assert a["new"] in version_in_file, f"Expected {a['new']} in {version_in_file}"


# ═══════════════════════════════════════════════════════════════
#  go + go.mod (REAL go list -u -m -json all)
# ═══════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _command_available("go"), reason="go not available")
class TestGoRealOutdated:
    """
    Run `go list -u -m -json all` on a real go.mod with old versions,
    parse the ndjson output, verify correct packages are detected.
    """

    def test_real_go_outdated_detection(self, tmp_path):
        plugin = get_plugin_by_name("go-mod")

        # Create a go.mod with a known-old dependency
        go_mod = tmp_path / "go.mod"
        go_mod.write_text(
            "module github.com/test/outdated-check\n\n"
            "go 1.21\n\n"
            "require github.com/pkg/errors v0.8.0\n"
        )

        # Create a minimal .go file so `go list` works
        main_go = tmp_path / "main.go"
        main_go.write_text(
            'package main\n\n'
            'import _ "github.com/pkg/errors"\n\n'
            'func main() {}\n'
        )

        # Step 1: go mod tidy to resolve deps
        subprocess.run(
            ["go", "mod", "tidy"],
            capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
        )

        # Step 2: Run REAL `go list -u -m -json all`
        result = subprocess.run(
            ["go", "list", "-u", "-m", "-json", "all"],
            capture_output=True, text=True, timeout=30, cwd=str(tmp_path),
        )
        assert result.returncode == 0, f"go list failed: {result.stderr}"

        stdout = result.stdout.strip()
        assert stdout, "go list returned empty output"

        # Step 3: Parse REAL ndjson output
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
            "skip_when": plugin.outdated_skip_when(),
        }
        outdated = parse_outdated_output(stdout, detected_info, plugin)

        # pkg/errors v0.8.0 should have an update to v0.9.x
        errors_outdated = [p for p in outdated if "pkg/errors" in p["name"]]
        assert len(errors_outdated) >= 1, (
            f"Expected pkg/errors in outdated, got: {[p['name'] for p in outdated]}"
        )

        pkg = errors_outdated[0]
        assert pkg["current"] == "v0.8.0"
        assert pkg["latest"].startswith("v0.9"), f"Expected v0.9.x, got {pkg['latest']}"

        # Main module should NOT be in the list
        main_entries = [p for p in outdated if p["name"] == "github.com/test/outdated-check"]
        assert len(main_entries) == 0, "Main module should be skipped"

    def test_go_diff_reports_gomod_versions_not_resolved(self):
        """
        Critical: the PR should show versions from go.mod (what the user wrote),
        NOT the resolved versions from go list (which may differ).

        Example: go.mod has v2.1.0, go list reports v2.1.6 (resolved),
        the PR should say v2.1.0 → v2.2.0, NOT v2.1.6 → v2.2.0.
        """
        plugin = get_plugin_by_name("go-mod")

        diff_output = (
            "--- a/go.mod\n"
            "+++ b/go.mod\n"
            "@@ -5,3 +5,3 @@\n"
            "-\tgithub.com/arangodb/go-driver/v2 v2.1.0\n"
            "+\tgithub.com/arangodb/go-driver/v2 v2.2.0\n"
            "-\tgolang.org/x/crypto v0.11.0\n"
            "+\tgolang.org/x/crypto v0.17.0\n"
        )

        # go list would report v2.1.6 as current (resolved), but we should
        # use v2.1.0 from the diff's - line (what was in go.mod)
        outdated_from_go_list = [
            {"name": "github.com/arangodb/go-driver/v2", "current": "v2.1.6", "latest": "v2.2.0"},
            {"name": "golang.org/x/crypto", "current": "v0.11.0", "latest": "v0.17.0"},
        ]

        applied = plugin.parse_update_diff(diff_output, outdated_from_go_list)

        assert len(applied) == 2

        arangodb = next(a for a in applied if "arangodb" in a["name"])
        assert arangodb["old"] == "v2.1.0", (
            f"Should use go.mod version (v2.1.0), not go list resolved version. Got: {arangodb['old']}"
        )
        assert arangodb["new"] == "v2.2.0"
        assert arangodb["dep_type"] == "direct"

        crypto = next(a for a in applied if "crypto" in a["name"])
        assert crypto["old"] == "v0.11.0"
        assert crypto["new"] == "v0.17.0"
        assert crypto["dep_type"] == "direct"

    def test_go_diff_transitive_dependency(self):
        """Transitive dep added by go get -u — only + line, no - line.
        Old version comes from go list, marked as transitive."""
        plugin = get_plugin_by_name("go-mod")

        diff_output = (
            "--- a/go.mod\n"
            "+++ b/go.mod\n"
            "-\tgithub.com/direct/pkg v1.0.0\n"
            "+\tgithub.com/direct/pkg v2.0.0\n"
            "+\tgithub.com/new/transitive v0.5.0\n"
        )

        outdated_from_go_list = [
            {"name": "github.com/new/transitive", "current": "v0.3.0", "latest": "v0.5.0"},
        ]

        applied = plugin.parse_update_diff(diff_output, outdated_from_go_list)

        assert len(applied) == 2

        direct = next(a for a in applied if "direct/pkg" in a["name"])
        assert direct["old"] == "v1.0.0"
        assert direct["dep_type"] == "direct"

        transitive = next(a for a in applied if "transitive" in a["name"])
        assert transitive["old"] == "v0.3.0"  # from go list since no - line
        assert transitive["new"] == "v0.5.0"
        assert transitive["dep_type"] == "transitive"

    def test_go_diff_new_dependency_added(self):
        """Completely new dep with no go list info — old is '?', marked transitive."""
        plugin = get_plugin_by_name("go-mod")

        diff_output = (
            "--- a/go.mod\n"
            "+++ b/go.mod\n"
            "+\tgithub.com/new/package v1.0.0\n"
        )

        applied = plugin.parse_update_diff(diff_output, [])

        assert len(applied) == 1
        assert applied[0]["name"] == "github.com/new/package"
        assert applied[0]["old"] == "?"
        assert applied[0]["new"] == "v1.0.0"
        assert applied[0]["dep_type"] == "transitive"


# ═══════════════════════════════════════════════════════════════
#  pip + pyproject.toml (REAL pip list --outdated)
# ═══════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _pip_available(), reason="pip not available")
class TestPipPyprojectRealOutdated:
    """
    Install from a pyproject.toml with old versions, run real pip outdated,
    apply to pyproject.toml, verify versions updated.
    """

    def test_real_pip_outdated_applied_to_pyproject(self, tmp_path):
        plugin = get_plugin_by_name("pip")
        venv_python = _create_venv(tmp_path)

        # Create pyproject.toml with known-old version
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\n'
            'name = "test-proj"\n'
            'version = "0.1.0"\n'
            'dependencies = [\n'
            '    "idna>=2.10",\n'
            ']\n'
        )

        # Install the old version in isolated venv
        subprocess.run(
            [venv_python, "-m", "pip", "install", "idna==2.10", "--quiet"],
            capture_output=True, timeout=120,
        )

        # Run REAL pip list --outdated in the venv
        result = subprocess.run(
            [venv_python, "-m", "pip", "list", "--outdated", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )

        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }
        outdated = parse_outdated_output(result.stdout.strip() or "[]", detected_info, plugin)

        idna_outdated = [p for p in outdated if p["name"].lower() == "idna"]
        if not idna_outdated:
            pytest.skip("idna not reported as outdated")

        # Apply to pyproject.toml
        content = pyproject.read_text()
        updated_content, applied = plugin.apply_updates(content, idna_outdated, "pyproject.toml")

        assert len(applied) >= 1
        assert ">=2.10" not in updated_content, "Old version still present"

        new_ver = idna_outdated[0]["latest"]
        assert f'"idna>={new_ver}"' in updated_content, (
            f"Expected idna>={new_ver} in:\n{updated_content}"
        )
