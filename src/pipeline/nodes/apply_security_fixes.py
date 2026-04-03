"""
Apply Security Fixes node — deterministic, 0 LLM tokens.

When the security audit finds CVEs with known fix versions:
1. Apply fixes via the ecosystem plugin (same as prepare node)
2. For unfixable CVEs, add TODO comments to the dependency file
3. Pass results to create_pr for a security-fix PR

All fix logic is plugin-driven — this node never names a specific tool.
"""

import os
from collections import defaultdict
from pathlib import Path

from src.ecosystems import get_plugin_by_name
from src.pipeline.state import PipelineState
from src.utils.env import get_pipeline_env
from src.utils.subprocess import run_cmd


def apply_security_fixes_node(state: PipelineState) -> dict:
    """
    Apply security fixes from audit findings.

    For each CVE with a fix_version:
      - Build an update entry and apply via plugin.apply_updates() or security_fix_command()
    For each CVE without a fix_version:
      - Add a TODO comment via plugin.add_todo_comment()
    """
    repo_path = state.get("repo_path", "")
    package_manager = state.get("package_manager", "")
    audit_results = state.get("audit_results") or []
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("apply_security_fixes")

    try:
        plugin = get_plugin_by_name(package_manager)
        if not plugin:
            return {}

        # Flatten all findings across audit sources
        all_findings = []
        for result in audit_results:
            all_findings.extend(result.get("findings", []))

        # Separate fixable vs unfixable
        # Deduplicate by package — pick the highest fix version per package
        fixable_by_pkg = defaultdict(list)
        unfixable = []

        for f in all_findings:
            fix_versions = f.get("fix_versions", [])
            if fix_versions:
                fixable_by_pkg[f["package"]].append(f)
            else:
                unfixable.append(f)

        if not fixable_by_pkg and not unfixable:
            return {}

        # Build update list — one entry per package with the highest fix version
        updates = []
        for pkg, findings in fixable_by_pkg.items():
            # Collect all fix versions for this package, pick the highest
            all_fix_versions = []
            for f in findings:
                all_fix_versions.extend(f.get("fix_versions", []))
            if not all_fix_versions:
                continue
            # Sort versions and pick the highest
            best_fix = sorted(all_fix_versions, key=_version_sort_key)[-1]
            current = findings[0].get("current_version", "")
            vulns = [f.get("vulnerability", "") for f in findings]

            updates.append({
                "name": pkg,
                "current": current,
                "latest": best_fix,
                "vulnerabilities": vulns,
            })

        security_fixes = []
        unfixable_cves = [
            {"package": f["package"], "vulnerability": f["vulnerability"], "detail": f.get("detail", "")}
            for f in unfixable
            if f.get("vulnerability") != "audit_finding"  # Skip raw fallback findings
        ]

        if updates:
            print(f"  [security_fix] Applying {len(updates)} security fix(es):")
            for u in updates:
                print(f"  [security_fix]   {u['name']}: {u['current']} → {u['latest']} ({', '.join(u['vulnerabilities'])})")

            if plugin.updates_via_command:
                # Command-based (go, cargo) — run security_fix_command per package
                for u in updates:
                    fix_cmd = plugin.security_fix_command(u["name"], u["latest"])
                    if fix_cmd:
                        try:
                            fix_cmd = plugin.fix_command(fix_cmd, repo_path=repo_path)
                        except TypeError:
                            fix_cmd = plugin.fix_command(fix_cmd)
                        print(f"  [security_fix] Running: {fix_cmd}")
                        result = run_cmd(
                            fix_cmd, timeout=120, cwd=repo_path, env=get_pipeline_env(),
                        )
                        if result.returncode == 0:
                            security_fixes.append({
                                "name": u["name"], "old": u["current"],
                                "new": u["latest"], "vulnerability": ", ".join(u["vulnerabilities"]),
                            })
                        else:
                            print(f"  [security_fix] Failed: {result.stderr[:200]}")
            else:
                # File-based (pip, npm) — use apply_updates
                repo_files = {p.name for p in Path(repo_path).iterdir() if p.is_file()}
                dep_file = plugin.resolve_dependency_file(repo_files)
                if dep_file:
                    dep_path = os.path.join(repo_path, dep_file)
                    if os.path.exists(dep_path):
                        with open(dep_path, "r") as f:
                            content = f.read()

                        updated_content, applied = plugin.apply_updates(content, updates, file_name=dep_file)

                        # Track which updates were applied and which weren't
                        applied_names = {a["name"].lower() for a in applied}
                        for u in updates:
                            if u["name"].lower() not in applied_names:
                                # Package not in dependency file (transitive dep)
                                print(f"  [security_fix] {u['name']}: not a direct dependency, cannot fix via {dep_file}")
                                for vuln_id in u["vulnerabilities"]:
                                    unfixable_cves.append({
                                        "package": u["name"],
                                        "vulnerability": vuln_id,
                                        "detail": f"Fix version {u['latest']} available but {u['name']} is not a direct dependency in {dep_file}",
                                    })

                        # Add TODO comments for unfixable CVEs (only for packages in the file)
                        for uf in unfixable_cves:
                            if uf["package"].lower() in {line.lower() for line in content.split("\n") if uf["package"].lower() in line.lower()}:
                                updated_content = plugin.add_todo_comment(
                                    updated_content, uf["package"], uf["vulnerability"], file_name=dep_file
                                )

                        if applied or (updated_content != content):
                            with open(dep_path, "w") as f:
                                f.write(updated_content)

                        for a in applied:
                            matching_vulns = fixable_by_pkg.get(a["name"], [])
                            vuln_ids = [f.get("vulnerability", "") for f in matching_vulns]
                            security_fixes.append({
                                "name": a["name"], "old": a.get("old", ""),
                                "new": a.get("new", ""), "vulnerability": ", ".join(vuln_ids),
                            })

        if security_fixes:
            print(f"  [security_fix] Applied {len(security_fixes)} fix(es)")
        if unfixable_cves:
            print(f"  [security_fix] {len(unfixable_cves)} CVE(s) with no fix available (TODO added)")

        if tracker:
            tracker.record_tool_call("apply_security_fixes")

        return {
            "security_fixes_applied": security_fixes,
            "unfixable_cves": unfixable_cves,
            "applied_updates": (state.get("applied_updates") or []) + security_fixes,
        }

    except Exception as e:
        print(f"  [security_fix] Error: {e}")
        return {}
    finally:
        if tracker:
            tracker.end_phase()


def _version_sort_key(version: str):
    """Sort key for version strings like '25.1', 'v5.16.5', '>=1.2.3'."""
    cleaned = version.lstrip("v>=~^")
    parts = []
    for p in cleaned.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return parts
