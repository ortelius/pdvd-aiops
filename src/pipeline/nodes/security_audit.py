"""
Security Audit node — deterministic, 0 LLM tokens.

Runs security checks after dependency updates:
1. Ecosystem-specific audit (npm audit, pip-audit, cargo audit, govulncheck)
   via the ecosystem plugin's audit_command() method
2. Universal security scanners detected in the integration registry
   (trivy, osv-scanner, etc.)

Both sources are combined into a single audit_results list.
"""

import os
import subprocess

from src.ecosystems import get_plugin_by_name
from src.integrations.registry import run_integration
from src.pipeline.state import PipelineState
from src.utils.env import get_pipeline_env
from src.utils.subprocess import run_cmd


def security_audit_node(state: PipelineState) -> dict:
    """
    Run security audits: ecosystem-specific + universal scanners.

    Returns: audit_results
    """
    repo_path = state.get("repo_path")
    package_manager = state.get("package_manager")
    detected = state.get("detected_integrations") or []
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("security_audit")

    results = []

    try:
        # ── 1. Ecosystem-specific audit (plugin-driven) ──────
        plugin = get_plugin_by_name(package_manager) if package_manager else None
        if plugin:
            audit_cmd = plugin.audit_command()
            if audit_cmd:
                # Fix command for current system (uses venv python if available)
                try:
                    audit_cmd = plugin.fix_command(audit_cmd, repo_path=repo_path)
                except TypeError:
                    audit_cmd = plugin.fix_command(audit_cmd)

                auto_installed = False

                # Try running the audit command directly; auto-install if it fails
                print(f"  [security_audit] Running ecosystem audit: {audit_cmd}")
                try:
                    proc = run_cmd(
                        audit_cmd, timeout=120, cwd=repo_path, env=get_pipeline_env(),
                    )
                except (subprocess.TimeoutExpired, Exception):
                    proc = None

                # If command not found / module missing, try auto-install
                error_text = (proc.stderr or "").lower() if proc else ""
                needs_install = (
                    proc is None
                    or (proc.returncode != 0 and (
                        "not found" in error_text
                        or "no module named" in error_text
                        or "command not found" in error_text
                    ))
                )
                if needs_install:
                    install_cmd = plugin.audit_install_command()
                    if install_cmd:
                        # Fix install command too (pip → python -m pip)
                        try:
                            install_cmd = plugin.fix_command(install_cmd, repo_path=repo_path)
                        except TypeError:
                            install_cmd = plugin.fix_command(install_cmd)

                        print(f"  [security_audit] Audit tool not found, auto-installing...")
                        try:
                            install_result = run_cmd(
                                install_cmd, timeout=120, env=get_pipeline_env(),
                            )
                            if install_result.returncode == 0:
                                auto_installed = True
                                print(f"  [security_audit] Installed successfully, retrying audit...")
                                proc = run_cmd(
                                    audit_cmd, timeout=120, cwd=repo_path, env=get_pipeline_env(),
                                )
                            else:
                                print(f"  [security_audit] Install failed: {install_result.stderr[:200]}")
                        except Exception as e:
                            print(f"  [security_audit] Install error: {e}")

                if proc and proc.returncode is not None:
                    findings = plugin.parse_audit_output(proc.stdout or "", proc.stderr or "")
                    status = "pass" if proc.returncode == 0 else "warning"

                    results.append({
                        "source": f"{package_manager}_audit",
                        "status": status,
                        "findings": findings,
                        "finding_count": len(findings),
                        "stdout": proc.stdout[-3000:] if proc.stdout else "",
                        "stderr": proc.stderr[-1000:] if proc.stderr else "",
                    })
                    print(f"  [security_audit] {package_manager}: {len(findings)} findings")

                # Auto-uninstall if we installed it
                if auto_installed and plugin.audit_uninstall_command():
                    uninstall_cmd = plugin.audit_uninstall_command()
                    try:
                        uninstall_cmd = plugin.fix_command(uninstall_cmd, repo_path=repo_path)
                    except TypeError:
                        uninstall_cmd = plugin.fix_command(uninstall_cmd)
                    print(f"  [security_audit] Cleaning up audit tool...")
                    try:
                        run_cmd(
                            uninstall_cmd, timeout=60, env=get_pipeline_env(),
                        )
                    except Exception:
                        pass

                if tracker:
                    tracker.record_tool_call(f"audit_{package_manager}")

        # ── 2. Universal security scanners (registry-driven) ─
        # Include scanners that are runnable OR have install_command
        security_scanners = [
            i for i in detected
            if i.get("category") == "security_scanner"
               and (i.get("runnable") or i.get("install_command"))
        ]

        for scanner in security_scanners:
            name = scanner["name"]
            print(f"  [security_audit] Running scanner: {name}")

            result = run_integration(repo_path, scanner)
            results.append({
                "source": name,
                "status": result["status"],
                "findings": result.get("findings", []),
                "finding_count": len(result.get("findings", [])),
            })

            print(f"  [security_audit] {name}: {len(result.get('findings', []))} findings")

            if tracker:
                tracker.record_tool_call(f"scan_{name}")

        if not results:
            print(f"  [security_audit] No audit tools available")

        return {"audit_results": results}

    except Exception as e:
        print(f"  [security_audit] Error: {e}")
        return {"audit_results": results}
    finally:
        if tracker:
            tracker.end_phase()
