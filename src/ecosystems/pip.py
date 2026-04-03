"""pip / poetry / pipenv ecosystem plugins."""

import os
import re
import subprocess
import sys
import venv
from pathlib import Path
from typing import Optional

from src.ecosystems import EcosystemPlugin, register, Dependency
from src.utils.subprocess import run_cmd


@register
class PipPlugin(EcosystemPlugin):
    name = "pip"
    language = "python"
    detect_files = ["requirements.txt", "requirements.in", "pyproject.toml", "setup.py"]
    lock_files = []
    dependency_file = "requirements.txt"  # default, overridden by resolve_dependency_file

    def detect(self, repo_files: set[str]) -> bool:
        python_files = {"requirements.txt", "requirements.in", "pyproject.toml", "setup.py", "Pipfile"}
        has_python = bool(repo_files & python_files)
        has_poetry = "poetry.lock" in repo_files
        has_pipenv = "Pipfile.lock" in repo_files
        return has_python and not has_poetry and not has_pipenv

    def resolve_dependency_file(self, repo_files: set[str]) -> Optional[str]:
        """Pick the actual dependency file from what exists in the repo.
        Priority: requirements.txt > pyproject.toml > setup.cfg"""
        for candidate in ["requirements.txt", "pyproject.toml", "setup.cfg"]:
            if candidate in repo_files:
                return candidate
        return None

    def parse_dependencies(self, content: str) -> list[Dependency]:
        deps = []
        for line in content.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "==" in stripped:
                name, version = stripped.split("==", 1)
                deps.append(Dependency(name=name.strip(), current=version.strip(), latest=""))
            elif ">=" in stripped:
                name, version = stripped.split(">=", 1)
                deps.append(Dependency(name=name.strip(), current=version.strip(), latest=""))
        return deps

    def apply_updates(self, content: str, updates: list[dict], file_name: str = "") -> tuple[str, list[dict]]:
        """Dispatch to the right updater based on the actual file."""
        if file_name == "pyproject.toml" or (not file_name and "dependencies = [" in content):
            return self._apply_updates_pyproject(content, updates)
        return self._apply_updates_requirements(content, updates)

    def rollback_package(self, content: str, package_name: str, target_version: str, file_name: str = "") -> str:
        """Dispatch to the right rollback based on the actual file."""
        if file_name == "pyproject.toml" or (not file_name and "dependencies = [" in content):
            return self._rollback_pyproject(content, package_name, target_version)
        return self._rollback_requirements(content, package_name, target_version)

    # ── requirements.txt handling ────────────────────────────

    def _apply_updates_requirements(self, content: str, updates: list[dict]) -> tuple[str, list[dict]]:
        lines = content.split("\n")
        updated_lines = []
        applied = []
        updates_dict = {u["name"].lower(): u for u in updates}

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                updated_lines.append(line)
                continue

            pkg_name = None
            if "==" in stripped:
                pkg_name = stripped.split("==")[0].strip()
            elif ">=" in stripped:
                pkg_name = stripped.split(">=")[0].strip()
            else:
                pkg_name = stripped

            if pkg_name and pkg_name.lower() in updates_dict:
                update_info = updates_dict[pkg_name.lower()]
                new_version = update_info.get("latest", update_info.get("latest_version", ""))
                updated_lines.append(f"{pkg_name}=={new_version}")
                applied.append({
                    "name": pkg_name,
                    "old": update_info.get("current", "unknown"),
                    "new": new_version,
                })
            else:
                updated_lines.append(line)

        return "\n".join(updated_lines), applied

    def _rollback_requirements(self, content: str, package_name: str, target_version: str) -> str:
        lines = content.split("\n")
        updated_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                updated_lines.append(line)
                continue
            if "==" in stripped:
                name = stripped.split("==")[0].strip()
                if name.lower() == package_name.lower():
                    updated_lines.append(f"{name}=={target_version}")
                    continue
            updated_lines.append(line)
        return "\n".join(updated_lines)

    # ── pyproject.toml handling ──────────────────────────────

    def _apply_updates_pyproject(self, content: str, updates: list[dict]) -> tuple[str, list[dict]]:
        lines = content.split("\n")
        updated_lines = []
        applied = []
        updates_dict = {u["name"].lower().replace("-", "_"): u for u in updates}
        # Also map with hyphens since Python package names can use either
        for u in updates:
            updates_dict[u["name"].lower().replace("_", "-")] = u
            updates_dict[u["name"].lower()] = u
        in_deps = False

        for line in lines:
            stripped = line.strip()

            # Detect dependency arrays: dependencies = [ or optional-dependencies.X = [
            if re.match(r'(dependencies|optional-dependencies\.\w+)\s*=\s*\[', stripped):
                in_deps = True
                updated_lines.append(line)
                continue
            if in_deps and stripped.startswith("]"):
                in_deps = False
                updated_lines.append(line)
                continue

            if in_deps and stripped.startswith('"'):
                # Match: "package>=version" or "package>=version,<max" or "package==version"
                match = re.match(
                    r'(\s*)"([a-zA-Z0-9_.@-]+)\[?[^\]]*\]?\s*(>=|==|~=)\s*([^",<>!]+)([^"]*)"(,?)',
                    line
                )
                if match:
                    indent = match.group(1)
                    pkg_name = match.group(2)
                    operator = match.group(3)
                    current_ver = match.group(4)
                    rest = match.group(5)  # e.g. ",<5.0" constraints
                    comma = match.group(6)

                    lookup = pkg_name.lower().replace("-", "_")
                    if lookup not in updates_dict:
                        lookup = pkg_name.lower().replace("_", "-")
                    if lookup not in updates_dict:
                        lookup = pkg_name.lower()

                    if lookup in updates_dict:
                        update_info = updates_dict[lookup]
                        new_ver = update_info.get("latest", update_info.get("latest_version", ""))
                        # Preserve extras like [standard] and operator
                        extras_match = re.search(r'(\[[^\]]+\])', line)
                        extras = extras_match.group(1) if extras_match else ""
                        updated_lines.append(f'{indent}"{pkg_name}{extras}{operator}{new_ver}"{comma}')
                        applied.append({"name": pkg_name, "old": current_ver, "new": new_ver})
                        continue

            updated_lines.append(line)

        return "\n".join(updated_lines), applied

    def _rollback_pyproject(self, content: str, package_name: str, target_version: str) -> str:
        lines = content.split("\n")
        updated_lines = []
        in_deps = False

        for line in lines:
            stripped = line.strip()
            if re.match(r'(dependencies|optional-dependencies\.\w+)\s*=\s*\[', stripped):
                in_deps = True
            if in_deps and stripped.startswith("]"):
                in_deps = False

            if in_deps:
                # Match the package name (case/hyphen insensitive)
                pattern = re.escape(package_name).replace(r'\-', '[-_]').replace(r'\_', '[-_]')
                match = re.match(
                    rf'(\s*)"({pattern})\[?[^\]]*\]?\s*(>=|==|~=)\s*([^",<>!]+)([^"]*)"(,?)',
                    line, re.IGNORECASE
                )
                if match:
                    indent = match.group(1)
                    pkg = match.group(2)
                    operator = match.group(3)
                    comma = match.group(6)
                    extras_match = re.search(r'(\[[^\]]+\])', line)
                    extras = extras_match.group(1) if extras_match else ""
                    updated_lines.append(f'{indent}"{pkg}{extras}{operator}{target_version}"{comma}')
                    continue

            updated_lines.append(line)
        return "\n".join(updated_lines)

    # ── Environment setup (venv) ────────────────────────────

    def setup_environment(self, repo_path: str) -> Optional[str]:
        """
        Create an isolated venv and install dependencies.

        Returns the path to the venv's python executable.
        """
        venv_dir = os.path.join(repo_path, ".venv")
        if os.path.exists(venv_dir):
            # Venv already exists (from a previous run or the repo itself)
            venv_python = self._get_venv_python(venv_dir)
            if venv_python and os.path.exists(venv_python):
                print(f"  [pip] Reusing existing venv at {venv_dir}")
                return venv_python

        print(f"  [pip] Creating venv at {venv_dir}...")
        try:
            venv.create(venv_dir, with_pip=True)
        except Exception as e:
            print(f"  [pip] Venv creation failed: {e}")
            return None

        venv_python = self._get_venv_python(venv_dir)
        if not venv_python:
            return None

        # Install dependencies
        repo_files = {p.name for p in Path(repo_path).iterdir() if p.is_file()}
        dep_file = self.resolve_dependency_file(repo_files)

        if dep_file:
            if dep_file == "requirements.txt":
                install_cmd = f"{venv_python} -m pip install -r requirements.txt --quiet"
            elif dep_file == "pyproject.toml":
                install_cmd = f"{venv_python} -m pip install -e . --quiet"
            else:
                install_cmd = f"{venv_python} -m pip install -r {dep_file} --quiet"

            print(f"  [pip] Installing deps from {dep_file}...")
            result = run_cmd(
                install_cmd, timeout=300, cwd=repo_path,
            )
            if result.returncode != 0:
                print(f"  [pip] Install warning: {result.stderr[-200:]}")

        return venv_python

    def teardown_environment(self, repo_path: str):
        """Remove the venv created by setup_environment."""
        import shutil
        venv_dir = os.path.join(repo_path, ".venv")
        if os.path.exists(venv_dir):
            shutil.rmtree(venv_dir, ignore_errors=True)

    def _get_venv_python(self, venv_dir: str) -> Optional[str]:
        """Get the python executable path for a venv."""
        if sys.platform == "win32":
            python_path = os.path.join(venv_dir, "Scripts", "python.exe")
        else:
            python_path = os.path.join(venv_dir, "bin", "python")
        return python_path if os.path.exists(python_path) else None

    # ── Command fixing ───────────────────────────────────

    def fix_command(self, command: str, repo_path: str = "") -> str:
        """
        Replace pip/pip3/pytest/pip-audit with python -m equivalents.

        If repo_path has a .venv, use the venv's python. Otherwise use sys.executable.
        """
        python = sys.executable

        # Use venv python if available
        if repo_path:
            venv_python = self._get_venv_python(os.path.join(repo_path, ".venv"))
            if venv_python and os.path.exists(venv_python):
                python = venv_python

        parts = command.split()
        if not parts:
            return command
        if parts[0] in ("pip", "pip3"):
            return f"{python} -m pip " + " ".join(parts[1:])
        if parts[0] == "pip-audit":
            return f"{python} -m pip_audit " + " ".join(parts[1:])
        if parts[0] == "pytest":
            return f"{python} -m pytest " + " ".join(parts[1:])
        return command

    def default_commands(self) -> dict:
        return {
            "install": "pip install -r requirements.txt",
            "build": "pip install -r requirements.txt",
            "test": "pytest",
            "lint": None,
        }

    def outdated_command(self) -> str:
        return "pip list --outdated --format json"

    def outdated_output_format(self) -> str:
        return "json_array"

    def outdated_field_map(self) -> dict:
        return {"name": "name", "current": "version", "latest": "latest_version"}

    def release_url(self, package_name: str, version: str) -> str:
        ver = version.lstrip("^~>=v")
        return f"https://pypi.org/project/{package_name}/{ver}/"

    def ci_build_patterns(self) -> list[str]:
        return [r'pip install', r'python.*setup\.py']

    def ci_test_patterns(self) -> list[str]:
        return [r'pytest', r'python.*-m\s+pytest', r'tox', r'nox']

    def ci_install_patterns(self) -> list[str]:
        return [r'pip install', r'pip3 install']

    def audit_command(self) -> str:
        return "pip-audit --format json"

    def audit_install_command(self) -> str:
        return "pip install pip-audit"

    def audit_uninstall_command(self) -> str:
        return "pip uninstall pip-audit -y"

    def audit_output_format(self) -> str:
        return "json"

    def parse_audit_output(self, stdout: str, stderr: str) -> list[dict]:
        import json as _json
        try:
            data = _json.loads(stdout)
            findings = []
            for entry in data.get("dependencies", data) if isinstance(data, dict) else data:
                if isinstance(entry, dict) and entry.get("vulns"):
                    for vuln in entry["vulns"]:
                        findings.append({
                            "package": entry.get("name", "unknown"),
                            "current_version": entry.get("version", ""),
                            "severity": vuln.get("aliases", ["unknown"])[0] if vuln.get("aliases") else "unknown",
                            "vulnerability": vuln.get("id", "unknown"),
                            "detail": vuln.get("description", "")[:500],
                            "fix_versions": vuln.get("fix_versions", []),
                        })
            return findings
        except (_json.JSONDecodeError, TypeError):
            return super().parse_audit_output(stdout, stderr)


@register
class PoetryPlugin(EcosystemPlugin):
    name = "poetry"
    language = "python"
    detect_files = ["pyproject.toml"]
    lock_files = ["poetry.lock"]
    dependency_file = "pyproject.toml"

    def detect(self, repo_files: set[str]) -> bool:
        return "poetry.lock" in repo_files

    def parse_dependencies(self, content: str) -> list[Dependency]:
        deps = []
        in_deps = False
        for line in content.split("\n"):
            if "[tool.poetry.dependencies]" in line or "[tool.poetry.dev-dependencies]" in line:
                in_deps = True
                continue
            if line.strip().startswith("[") and in_deps:
                in_deps = False
                continue
            if in_deps and "=" in line and not line.strip().startswith("#"):
                match = re.match(r'(\S+)\s*=\s*["\']([^"\']+)["\']', line.strip())
                if match:
                    deps.append(Dependency(name=match.group(1), current=match.group(2), latest=""))
        return deps

    def apply_updates(self, content: str, updates: list[dict], file_name: str = "") -> tuple[str, list[dict]]:
        lines = content.split("\n")
        updated_lines = []
        applied = []
        updates_dict = {u["name"].lower(): u for u in updates}
        in_deps = False

        for line in lines:
            if "[tool.poetry.dependencies]" in line or "[tool.poetry.dev-dependencies]" in line:
                in_deps = True
                updated_lines.append(line)
                continue
            if line.strip().startswith("[") and in_deps:
                in_deps = False

            if in_deps and "=" in line and not line.strip().startswith("#"):
                match = re.match(r'(\s*)(\S+)\s*=\s*["\']([^"\']+)["\']', line)
                if match:
                    indent, pkg_name, current = match.groups()
                    if pkg_name.lower() in updates_dict:
                        update_info = updates_dict[pkg_name.lower()]
                        new_version = update_info.get("latest", update_info.get("latest_version", ""))
                        updated_lines.append(f'{indent}{pkg_name} = "{new_version}"')
                        applied.append({"name": pkg_name, "old": current, "new": new_version})
                        continue

            updated_lines.append(line)

        return "\n".join(updated_lines), applied

    def rollback_package(self, content: str, package_name: str, target_version: str, file_name: str = "") -> str:
        lines = content.split("\n")
        updated_lines = []
        for line in lines:
            match = re.match(
                r'(\s*)(' + re.escape(package_name) + r')\s*=\s*["\']([^"\']+)["\']', line
            )
            if match:
                indent, name, _ = match.groups()
                updated_lines.append(f'{indent}{name} = "{target_version}"')
            else:
                updated_lines.append(line)
        return "\n".join(updated_lines)

    def default_commands(self) -> dict:
        return {
            "install": "poetry install",
            "build": "poetry install",
            "test": "poetry run pytest",
            "lint": None,
        }

    def outdated_command(self) -> str:
        return "poetry show --outdated"

    def outdated_output_format(self) -> str:
        return "text"

    def parse_outdated_text(self, stdout: str) -> list[dict]:
        """Poetry format: name current latest description..."""
        results = []
        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line or line.startswith(("!", "-", "=")):
                continue
            parts = line.split()
            if len(parts) >= 3:
                results.append({
                    "name": parts[0],
                    "current": parts[1],
                    "latest": parts[2],  # column 3, NOT last column
                })
        return results
