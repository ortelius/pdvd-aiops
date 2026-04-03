"""Go module ecosystem plugin."""

import re
from typing import Optional

from src.ecosystems import EcosystemPlugin, register, Dependency


def _extract_go_fix_versions(osv_entry: dict) -> list[str]:
    """Extract fix versions from OSV affected[].ranges[].events[{fixed}]."""
    fix_versions = []
    for affected in osv_entry.get("affected", []):
        for rng in affected.get("ranges", []):
            for event in rng.get("events", []):
                fixed = event.get("fixed")
                if fixed and fixed not in fix_versions:
                    fix_versions.append(fixed)
    return fix_versions


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
        """Parse git diff of go.mod to extract actual version changes.

        Uses BOTH - lines (old version from go.mod) and + lines (new version)
        to report what was actually in the file, not the resolved version.

        Marks packages as "direct" (had a - line in go.mod) or
        "transitive" (only a + line — pulled in by go get -u).
        """
        # First pass: collect old versions from removed lines
        old_versions = {}
        for line in diff_output.split("\n"):
            if line.startswith("-") and not line.startswith("---"):
                match = re.match(r'-[\s\t]+(\S+)\s+(v\S+)', line)
                if match:
                    old_versions[match.group(1)] = match.group(2)

        # Second pass: collect new versions from added lines
        applied = []
        for line in diff_output.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                match = re.match(r'\+[\s\t]+(\S+)\s+(v\S+)', line)
                if match:
                    pkg_name = match.group(1)
                    new_version = match.group(2)

                    if pkg_name in old_versions:
                        # Direct dependency — was in go.mod, version changed
                        old_version = old_versions[pkg_name]
                        dep_type = "direct"
                    else:
                        # Transitive — added by go get -u, not previously in go.mod
                        # Use go list's resolved version as old if available
                        outdated_map = {p["name"]: p for p in outdated_packages}
                        old_info = outdated_map.get(pkg_name, {})
                        old_version = old_info.get("current", "?")
                        dep_type = "transitive"

                    applied.append({
                        "name": pkg_name,
                        "old": old_version,
                        "new": new_version,
                        "dep_type": dep_type,
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

    def release_url(self, package_name: str, version: str) -> str:
        ver = version.lstrip("v")
        # Go packages on pkg.go.dev; GitHub-hosted ones also have releases
        if package_name.startswith("github.com/"):
            # Strip /vN module suffix for GitHub release URL
            repo_path = package_name
            for suffix in ["/v2", "/v3", "/v4", "/v5", "/v6", "/v7", "/v8", "/v9",
                          "/v10", "/v11", "/v12", "/v13", "/v14", "/v15"]:
                if repo_path.endswith(suffix):
                    repo_path = repo_path[:-len(suffix)]
                    break
            return f"https://{repo_path}/releases/tag/{version}"
        return f"https://pkg.go.dev/{package_name}@{version}"

    def ci_build_patterns(self) -> list[str]:
        return [r'go build']

    def ci_test_patterns(self) -> list[str]:
        return [r'go test']

    def ci_install_patterns(self) -> list[str]:
        return [r'go mod download']

    def security_fix_command(self, package_name: str, fix_version: str) -> str:
        return f"go get {package_name}@v{fix_version.lstrip('v')} && go mod tidy"

    def audit_command(self) -> str:
        return "govulncheck -json ./..."

    def audit_install_command(self) -> str:
        return "go install golang.org/x/vuln/cmd/govulncheck@latest"

    def audit_uninstall_command(self) -> str:
        return "rm -f $(go env GOPATH)/bin/govulncheck"

    def audit_output_format(self) -> str:
        return "json"

    def parse_audit_output(self, stdout: str, stderr: str) -> list[dict]:
        """
        Parse govulncheck -json output.

        govulncheck v1.1+ outputs a stream of JSON message objects:
          {"config": {...}}
          {"progress": {...}}
          {"osv": {...}}           ← vulnerability details (id, summary, aliases)
          {"finding": {...}}       ← affected module/package/symbol info

        We collect OSV entries (actual vulnerabilities) and match with findings.
        """
        import json as _json
        findings = []
        osv_map = {}  # id → osv entry
        seen = set()

        try:
            # Parse the ndjson stream — each line is a separate JSON object
            decoder = _json.JSONDecoder()
            pos = 0
            text = stdout.strip()
            finding_osv_ids = set()

            while pos < len(text):
                while pos < len(text) and text[pos] in " \t\n\r":
                    pos += 1
                if pos >= len(text):
                    break
                try:
                    obj, end_pos = decoder.raw_decode(text, pos)
                    pos = end_pos
                except _json.JSONDecodeError:
                    pos += 1
                    continue

                # Collect OSV vulnerability definitions
                osv = obj.get("osv")
                if osv and isinstance(osv, dict):
                    osv_id = osv.get("id", "")
                    if osv_id:
                        osv_map[osv_id] = osv

                # Collect findings (vulnerable code actually called)
                finding = obj.get("finding")
                if finding and isinstance(finding, dict):
                    osv_id = finding.get("osv", "")
                    if osv_id:
                        finding_osv_ids.add(osv_id)
                        if osv_id not in seen:
                            seen.add(osv_id)
                            osv_entry = osv_map.get(osv_id, {})
                            trace = finding.get("trace", [])
                            module = trace[0].get("module", "unknown") if trace else "unknown"
                            aliases = osv_entry.get("aliases", [])
                            cve = aliases[0] if aliases else osv_id

                            findings.append({
                                "package": module,
                                "severity": cve,
                                "vulnerability": osv_id,
                                "detail": osv_entry.get("summary", "")[:500],
                                "fix_versions": _extract_go_fix_versions(osv_entry),
                            })

            # Also report OSV advisories that affect deps even if
            # the vulnerable code path isn't called (informational)
            for osv_id, osv_entry in osv_map.items():
                if osv_id in seen:
                    continue
                seen.add(osv_id)
                aliases = osv_entry.get("aliases", [])
                cve = aliases[0] if aliases else osv_id
                affected = osv_entry.get("affected", [])
                pkg_name = affected[0].get("package", {}).get("name", "unknown") if affected else "unknown"
                called = "called" if osv_id in finding_osv_ids else "not called"

                findings.append({
                    "package": pkg_name,
                    "severity": f"{cve} ({called})",
                    "vulnerability": osv_id,
                    "detail": osv_entry.get("summary", "")[:500],
                    "fix_versions": _extract_go_fix_versions(osv_entry),
                })

        except Exception:
            pass

        return findings
