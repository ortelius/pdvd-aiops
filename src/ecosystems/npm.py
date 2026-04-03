"""npm ecosystem plugin."""

import json
from src.ecosystems import EcosystemPlugin, register, Dependency


@register
class NpmPlugin(EcosystemPlugin):
    name = "npm"
    language = "nodejs"
    detect_files = ["package.json"]
    lock_files = ["package-lock.json"]
    dependency_file = "package.json"

    def detect(self, repo_files: set[str]) -> bool:
        return "package.json" in repo_files and "yarn.lock" not in repo_files and "pnpm-lock.yaml" not in repo_files

    def parse_dependencies(self, content: str) -> list[Dependency]:
        data = json.loads(content)
        deps = []
        for section in ["dependencies", "devDependencies", "peerDependencies"]:
            for name, version in data.get(section, {}).items():
                deps.append(Dependency(name=name, current=version.lstrip("^~>="), latest=""))
        return deps

    def apply_updates(self, content: str, updates: list[dict], file_name: str = "") -> tuple[str, list[dict]]:
        data = json.loads(content)
        applied = []

        for section in ["dependencies", "devDependencies", "peerDependencies"]:
            if section not in data:
                continue
            for update in updates:
                pkg_name = update["name"]
                new_version = update.get("latest", update.get("latest_version", ""))
                if pkg_name in data[section]:
                    old_version = data[section][pkg_name]
                    prefix = ""
                    if old_version.startswith("^"):
                        prefix = "^"
                    elif old_version.startswith("~"):
                        prefix = "~"
                    elif old_version.startswith(">="):
                        prefix = ">="
                    data[section][pkg_name] = f"{prefix}{new_version}"
                    applied.append({
                        "name": pkg_name,
                        "old": update.get("current", old_version),
                        "new": new_version,
                        "section": section,
                    })

        return json.dumps(data, indent=2), applied

    def rollback_package(self, content: str, package_name: str, target_version: str, file_name: str = "") -> str:
        data = json.loads(content)
        for section in ["dependencies", "devDependencies", "peerDependencies"]:
            if section in data and package_name in data[section]:
                old_value = data[section][package_name]
                prefix = ""
                if old_value.startswith("^"):
                    prefix = "^"
                elif old_value.startswith("~"):
                    prefix = "~"
                data[section][package_name] = f"{prefix}{target_version}"
        return json.dumps(data, indent=2)

    def ci_build_patterns(self) -> list[str]:
        return [r'npm run build', r'npm ci']

    def ci_test_patterns(self) -> list[str]:
        return [r'npm test', r'npm run test']

    def ci_install_patterns(self) -> list[str]:
        return [r'npm install', r'npm ci']

    def default_commands(self) -> dict:
        return {
            "install": "npm install",
            "build": "npm run build",
            "test": "npm test",
            "lint": "npm run lint",
        }

    def release_url(self, package_name: str, version: str) -> str:
        ver = version.lstrip("^~>=v")
        return f"https://www.npmjs.com/package/{package_name}/v/{ver}"

    def outdated_command(self) -> str:
        return "npm outdated --json"

    def outdated_output_format(self) -> str:
        return "json_dict"

    def outdated_field_map(self) -> dict:
        return {"name": "_key", "current": "current", "latest": "latest"}

    def audit_command(self) -> str:
        return "npm audit --json"

    def audit_output_format(self) -> str:
        return "json"

    def parse_audit_output(self, stdout: str, stderr: str) -> list[dict]:
        try:
            data = json.loads(stdout)
            findings = []
            for vuln_id, info in data.get("vulnerabilities", {}).items():
                fix_available = info.get("fixAvailable", False)
                fix_versions = []
                if isinstance(fix_available, dict):
                    fix_versions = [fix_available.get("version", "")]
                findings.append({
                    "package": info.get("name", vuln_id),
                    "current_version": info.get("range", ""),
                    "severity": info.get("severity", "unknown"),
                    "vulnerability": vuln_id,
                    "detail": info.get("title", info.get("range", "")),
                    "fix_versions": [v for v in fix_versions if v],
                })
            return findings
        except (json.JSONDecodeError, AttributeError):
            return super().parse_audit_output(stdout, stderr)


@register
class YarnPlugin(EcosystemPlugin):
    name = "yarn"
    language = "nodejs"
    detect_files = ["package.json"]
    lock_files = ["yarn.lock"]
    dependency_file = "package.json"

    def detect(self, repo_files: set[str]) -> bool:
        return "yarn.lock" in repo_files

    def parse_dependencies(self, content: str) -> list[Dependency]:
        return NpmPlugin().parse_dependencies(content)

    def apply_updates(self, content: str, updates: list[dict], file_name: str = "") -> tuple[str, list[dict]]:
        return NpmPlugin().apply_updates(content, updates, file_name)

    def rollback_package(self, content: str, package_name: str, target_version: str, file_name: str = "") -> str:
        return NpmPlugin().rollback_package(content, package_name, target_version, file_name)

    def default_commands(self) -> dict:
        return {
            "install": "yarn install",
            "build": "yarn build",
            "test": "yarn test",
            "lint": "yarn run lint",
        }

    def outdated_command(self) -> str:
        return "yarn outdated"

    def outdated_output_format(self) -> str:
        return "text"

    def parse_outdated_text(self, stdout: str) -> list[dict]:
        """Parse yarn v1 outdated text format.

        Yarn outputs a preamble (version, color legend, info lines)
        followed by a table with header:
          Package  Current  Wanted  Latest  Package Type  URL
        We skip everything before the header and parse data rows.
        Column indices: 0=Package, 1=Current, 2=Wanted, 3=Latest.
        """
        results = []
        header_found = False

        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue

            # Detect the header row
            if not header_found:
                if "Package" in line and "Current" in line and "Latest" in line:
                    header_found = True
                continue

            # After header: skip footer lines
            if line.startswith("Done "):
                continue

            parts = line.split()
            if len(parts) >= 4:
                results.append({
                    "name": parts[0],
                    "current": parts[1],
                    "latest": parts[3],  # index 3 = Latest (index 2 = Wanted)
                })

        return results


@register
class PnpmPlugin(EcosystemPlugin):
    name = "pnpm"
    language = "nodejs"
    detect_files = ["package.json"]
    lock_files = ["pnpm-lock.yaml"]
    dependency_file = "package.json"

    def detect(self, repo_files: set[str]) -> bool:
        return "pnpm-lock.yaml" in repo_files

    def parse_dependencies(self, content: str) -> list[Dependency]:
        return NpmPlugin().parse_dependencies(content)

    def apply_updates(self, content: str, updates: list[dict], file_name: str = "") -> tuple[str, list[dict]]:
        return NpmPlugin().apply_updates(content, updates, file_name)

    def rollback_package(self, content: str, package_name: str, target_version: str, file_name: str = "") -> str:
        return NpmPlugin().rollback_package(content, package_name, target_version, file_name)

    def default_commands(self) -> dict:
        return {
            "install": "pnpm install",
            "build": "pnpm build",
            "test": "pnpm test",
            "lint": "pnpm run lint",
        }

    def outdated_command(self) -> str:
        return "pnpm outdated --format json"

    def outdated_output_format(self) -> str:
        return "json_dict"

    def outdated_field_map(self) -> dict:
        return {"name": "_key", "current": "current", "latest": "latest"}
