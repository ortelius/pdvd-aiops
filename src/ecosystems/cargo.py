"""Cargo (Rust) ecosystem plugin."""

import re
from typing import Optional

from src.ecosystems import EcosystemPlugin, register, Dependency


@register
class CargoPlugin(EcosystemPlugin):
    name = "cargo"
    language = "rust"
    detect_files = ["Cargo.toml"]
    lock_files = ["Cargo.lock"]
    dependency_file = "Cargo.toml"

    @property
    def updates_via_command(self) -> bool:
        return True

    def update_command(self, repo_path: str, outdated_packages: list[dict]) -> Optional[str]:
        return "cargo update"

    def parse_update_diff(self, diff_output: str, outdated_packages: list[dict]) -> list[dict]:
        """Parse git diff of Cargo.lock to extract actual version changes."""
        applied = []
        outdated_map = {p["name"].lower(): p for p in outdated_packages}

        for line in diff_output.split("\n"):
            if line.startswith("+") and "version =" in line:
                match = re.match(r'\+\s*version\s*=\s*"([^"]+)"', line)
                if match:
                    # Cargo.lock doesn't show package name on the version line,
                    # fall back to outdated list
                    pass

        if not applied:
            return super().parse_update_diff(diff_output, outdated_packages)
        return applied

    def detect(self, repo_files: set[str]) -> bool:
        return "Cargo.toml" in repo_files

    def parse_dependencies(self, content: str) -> list[Dependency]:
        deps = []
        in_deps = False
        for line in content.split("\n"):
            if line.strip().startswith("["):
                in_deps = "dependencies" in line.lower()
                continue
            if in_deps and "=" in line and not line.strip().startswith("#"):
                match = re.match(r'\s*([a-zA-Z0-9_-]+)\s*=\s*["\']([^"\']+)["\']', line)
                if match:
                    deps.append(Dependency(name=match.group(1), current=match.group(2), latest=""))
        return deps

    def apply_updates(self, content: str, updates: list[dict], file_name: str = "") -> tuple[str, list[dict]]:
        # Cargo uses cargo update command, but Cargo.toml can also be edited for version pins
        lines = content.split("\n")
        updated_lines = []
        applied = []
        updates_dict = {u["name"].lower(): u for u in updates}
        in_deps = False

        for line in lines:
            if line.strip().startswith("["):
                in_deps = "dependencies" in line.lower()

            if in_deps and "=" in line and not line.strip().startswith("#"):
                match = re.match(r'(\s*)([a-zA-Z0-9_-]+)\s*=\s*["\']([^"\']+)["\']', line)
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
            "install": "cargo fetch",
            "build": "cargo build",
            "test": "cargo test",
            "lint": "cargo clippy",
        }

    def outdated_command(self) -> str:
        return "cargo outdated"

    def outdated_output_format(self) -> str:
        return "text"

    def parse_outdated_text(self, stdout: str) -> list[dict]:
        """cargo outdated format: Name Project Compat Latest Kind"""
        results = []
        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line or line.startswith(("-", "=", "Name")):
                continue
            parts = line.split()
            if len(parts) >= 4:
                results.append({
                    "name": parts[0],
                    "current": parts[1],
                    "latest": parts[3],  # column 4 is Latest
                })
        return results

    def release_url(self, package_name: str, version: str) -> str:
        ver = version.lstrip("^~>=v")
        return f"https://crates.io/crates/{package_name}/{ver}"

    def ci_build_patterns(self) -> list[str]:
        return [r'cargo build']

    def ci_test_patterns(self) -> list[str]:
        return [r'cargo test']

    def ci_install_patterns(self) -> list[str]:
        return [r'cargo fetch']

    def audit_command(self) -> str:
        return "cargo audit --json"

    def audit_install_command(self) -> str:
        return "cargo install cargo-audit"

    def audit_uninstall_command(self) -> str:
        return "cargo uninstall cargo-audit"

    def audit_output_format(self) -> str:
        return "json"

    def parse_audit_output(self, stdout: str, stderr: str) -> list[dict]:
        import json as _json
        try:
            data = _json.loads(stdout)
            findings = []
            for vuln in data.get("vulnerabilities", {}).get("list", []):
                advisory = vuln.get("advisory", {})
                pkg = vuln.get("package", {})
                findings.append({
                    "package": pkg.get("name", "unknown"),
                    "severity": advisory.get("cvss", "unknown"),
                    "vulnerability": advisory.get("id", "unknown"),
                    "detail": advisory.get("title", "")[:500],
                })
            return findings
        except (_json.JSONDecodeError, AttributeError):
            return super().parse_audit_output(stdout, stderr)
