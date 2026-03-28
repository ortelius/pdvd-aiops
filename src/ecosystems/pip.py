"""pip / poetry / pipenv ecosystem plugins."""

import re
import sys
from typing import Optional

from src.ecosystems import EcosystemPlugin, register, Dependency


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

    def fix_command(self, command: str) -> str:
        """Replace pip/pip3/pytest with python -m equivalents."""
        parts = command.split()
        if not parts:
            return command
        if parts[0] in ("pip", "pip3"):
            return f"{sys.executable} -m pip " + " ".join(parts[1:])
        if parts[0] == "pytest":
            return f"{sys.executable} -m pytest " + " ".join(parts[1:])
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

    def ci_build_patterns(self) -> list[str]:
        return [r'pip install', r'python.*setup\.py']

    def ci_test_patterns(self) -> list[str]:
        return [r'pytest', r'python.*-m\s+pytest', r'tox', r'nox']

    def ci_install_patterns(self) -> list[str]:
        return [r'pip install', r'pip3 install']


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
