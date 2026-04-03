"""Dependency manager integration definitions."""

import json
from src.integrations.registry import register_integration


def _parse_renovate_output(stdout: str, stderr: str) -> list[dict]:
    """
    Parse Renovate JSON log output to extract update information.

    Renovate with LOG_FORMAT=json outputs one JSON object per line.
    Updates are found in log entries containing package update data.
    """
    updates = []
    seen = set()

    # Renovate logs go to both stdout and stderr depending on version
    for source in [stdout, stderr]:
        if not source:
            continue
        for line in source.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Extract from "packageFiles with updates" log entry
            config = entry.get("config") or {}
            for manager_key, package_files in config.items():
                if not isinstance(package_files, list):
                    continue
                for pf in package_files:
                    if not isinstance(pf, dict):
                        continue
                    for dep in pf.get("deps", []):
                        if not isinstance(dep, dict):
                            continue
                        for update in dep.get("updates", []):
                            dep_name = dep.get("depName") or dep.get("packageName") or ""
                            update_type = update.get("updateType", "")
                            new_value = update.get("newValue") or update.get("newVersion") or ""
                            new_digest = update.get("newDigest", "")
                            current_value = dep.get("currentValue") or dep.get("currentVersion") or ""
                            current_digest = dep.get("currentDigest", "")
                            dep_type = dep.get("depType", "")

                            # Build change string
                            if new_digest and not new_value:
                                old_short = current_digest[:7] if current_digest else "?"
                                new_short = new_digest[:7]
                                change = f"`{old_short}` → `{new_short}`"
                            elif new_value:
                                change = f"`{current_value}` → `{new_value}`"
                            else:
                                change = ""

                            key = (dep_name, update_type, new_value or new_digest[:7])
                            if key in seen:
                                continue
                            seen.add(key)

                            updates.append({
                                "package": dep_name,
                                "severity": update_type,
                                "vulnerability": dep_type,
                                "detail": change,
                                # Extra fields for rich rendering
                                "dep_type": dep_type,
                                "update_type": update_type,
                                "current": current_value or (current_digest[:7] if current_digest else ""),
                                "new": new_value or (new_digest[:7] if new_digest else ""),
                                "new_digest": new_digest[:7] if new_digest else "",
                            })

            # Also check top-level "res" entries (some Renovate versions)
            if "depName" in entry and "updates" in entry:
                dep = entry
                for update in dep.get("updates", []):
                    dep_name = dep.get("depName", "")
                    update_type = update.get("updateType", "")
                    new_value = update.get("newValue", "")
                    current_value = dep.get("currentValue", "")

                    key = (dep_name, update_type, new_value)
                    if key in seen:
                        continue
                    seen.add(key)

                    updates.append({
                        "package": dep_name,
                        "severity": update_type,
                        "vulnerability": dep.get("depType", ""),
                        "detail": f"`{current_value}` → `{new_value}`",
                        "dep_type": dep.get("depType", ""),
                        "update_type": update_type,
                        "current": current_value,
                        "new": new_value,
                    })

    return updates


register_integration(
    name="renovate",
    category="dependency_manager",
    config_files=["renovate.json", "renovate.json5", ".renovaterc", ".renovaterc.json", ".github/renovate.json"],
    run_command="LOG_FORMAT=json LOG_LEVEL=debug renovate --platform=local --dry-run=lookup --base-dir={repo_path}",
    detect_command="renovate --version",
    install_command="npm install -g renovate",
    uninstall_command="npm uninstall -g renovate",
    output_format="json",
    parse_output=_parse_renovate_output,
    ecosystem=None,
    severity="info",
    description="Automated dependency update manager",
)

register_integration(
    name="pre-commit",
    category="dependency_manager",
    config_files=[".pre-commit-config.yaml"],
    run_command="pre-commit run --all-files",
    detect_command="pre-commit --version",
    output_format="text",
    ecosystem=None,
    severity="warning",
    description="Git hook manager for code quality checks",
)

register_integration(
    name="commitlint",
    category="dependency_manager",
    config_files=[".commitlintrc", ".commitlintrc.js", ".commitlintrc.json", ".commitlintrc.yml", "commitlint.config.js"],
    run_command="npx commitlint --from HEAD~1",
    detect_command="npx commitlint --version",
    output_format="text",
    ecosystem="nodejs",
    severity="info",
    description="Commit message convention enforcer",
)
