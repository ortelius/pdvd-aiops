"""Go module ecosystem plugin."""

import re
from typing import Optional

from src.ecosystems import EcosystemPlugin, register, Dependency


@register
class GoPlugin(EcosystemPlugin):
    name = "go-mod"
    language = "go"
    detect_files = ["go.mod"]
    lock_files = ["go.sum"]
    dependency_file = "go.mod"

    @property
    def updates_via_command(self) -> bool:
        return True

    def update_command(self, repo_path: str, outdated_packages: list[dict]) -> Optional[str]:
        return "go get -u ./..."

    def post_update_command(self) -> Optional[str]:
        return "go mod tidy"

    def parse_update_diff(self, diff_output: str, outdated_packages: list[dict]) -> list[dict]:
        """Parse git diff of go.mod to extract actual version changes."""
        applied = []
        outdated_map = {p["name"]: p for p in outdated_packages}

        for line in diff_output.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                match = re.match(r'\+\s+(\S+)\s+(v\S+)', line)
                if match:
                    pkg_name = match.group(1)
                    new_version = match.group(2)
                    old_info = outdated_map.get(pkg_name, {})
                    applied.append({
                        "name": pkg_name,
                        "old": old_info.get("current", "?"),
                        "new": new_version,
                    })

        if not applied:
            return super().parse_update_diff(diff_output, outdated_packages)
        return applied

    @property
    def rollback_via_command(self) -> bool:
        return True

    def rollback_command(self, package_name: str, target_version: str) -> Optional[str]:
        return f"go get {package_name}@{target_version} && go mod tidy"

    def detect(self, repo_files: set[str]) -> bool:
        return "go.mod" in repo_files

    def parse_dependencies(self, content: str) -> list[Dependency]:
        deps = []
        in_require = False
        for line in content.split("\n"):
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
                    deps.append(Dependency(name=parts[0], current=parts[1], latest=""))
        return deps

    def apply_updates(self, content: str, updates: list[dict], file_name: str = "") -> tuple[str, list[dict]]:
        # Go modules are updated via commands, not file editing
        return content, []

    def rollback_package(self, content: str, package_name: str, target_version: str, file_name: str = "") -> str:
        # Go rollback is done via commands, not file editing
        return content

    def default_commands(self) -> dict:
        return {
            "install": "go mod download",
            "build": "go build ./...",
            "test": "go test ./...",
            "lint": "go vet ./...",
        }

    def outdated_command(self) -> str:
        return "go list -u -m -json all"

    def outdated_output_format(self) -> str:
        return "ndjson"

    def outdated_field_map(self) -> dict:
        return {"name": "Path", "current": "Version", "latest": "Update.Version"}

    def outdated_skip_when(self) -> dict:
        return {"Main": True, "Update": None}

    def ci_build_patterns(self) -> list[str]:
        return [r'go build']

    def ci_test_patterns(self) -> list[str]:
        return [r'go test']

    def ci_install_patterns(self) -> list[str]:
        return [r'go mod download']
