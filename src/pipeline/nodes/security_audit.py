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
import shutil
import subprocess
import sys

from src.ecosystems import get_plugin_by_name
from src.integrations.registry import run_integration
from src.pipeline.state import PipelineState


def _get_env():
    """Get environment with PATH that includes tool binary directories."""
    env = os.environ.copy()
    extra_paths = []

    # Python bin
    python_bin = os.path.dirname(sys.executable)
    if python_bin not in env.get("PATH", ""):
        extra_paths.append(python_bin)

    # Go bin (GOPATH/bin)
    gopath = env.get("GOPATH") or os.path.expanduser("~/go")
    gobin = os.path.join(gopath, "bin")
    if gobin not in env.get("PATH", ""):
        extra_paths.append(gobin)

    # Cargo bin (~/.cargo/bin)
    cargo_bin = os.path.expanduser("~/.cargo/bin")
    if os.path.isdir(cargo_bin) and cargo_bin not in env.get("PATH", ""):
        extra_paths.append(cargo_bin)

    if extra_paths:
        env["PATH"] = os.pathsep.join(extra_paths) + os.pathsep + env.get("PATH", "")

    # Renovate expects GITHUB_TOKEN
    if not env.get("GITHUB_TOKEN") and env.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
        env["GITHUB_TOKEN"] = env["GITHUB_PERSONAL_ACCESS_TOKEN"]

    return env


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
                audit_cmd = plugin.fix_command(audit_cmd)
                auto_installed = False

                # Auto-install audit tool if not available
                audit_bin = audit_cmd.split()[0]
                env = _get_env()
                path_dirs = env.get("PATH", "")
                if not shutil.which(audit_bin, path=path_dirs) and plugin.audit_install_command():
                    print(f"  [security_audit] {audit_bin} not found, auto-installing...")
                    try:
                        install_result = subprocess.run(
                            plugin.audit_install_command(), shell=True,
                            capture_output=True, text=True, timeout=120, env=_get_env(),
                        )
                        if install_result.returncode == 0:
                            auto_installed = True
                            print(f"  [security_audit] {audit_bin} installed successfully")
                        else:
                            print(f"  [security_audit] {audit_bin} install failed: {install_result.stderr[:200]}")
                    except Exception as e:
                        print(f"  [security_audit] {audit_bin} install error: {e}")

                if shutil.which(audit_bin, path=_get_env().get("PATH", "")) or auto_installed:
                    print(f"  [security_audit] Running ecosystem audit: {audit_cmd}")
                    try:
                        proc = subprocess.run(
                            audit_cmd, shell=True, capture_output=True, text=True,
                            timeout=120, cwd=repo_path, env=_get_env(),
                        )

                        findings = plugin.parse_audit_output(proc.stdout, proc.stderr)
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

                    except subprocess.TimeoutExpired:
                        results.append({
                            "source": f"{package_manager}_audit",
                            "status": "error",
                            "findings": [],
                            "finding_count": 0,
                        })
                    except Exception as e:
                        print(f"  [security_audit] {package_manager} audit error: {e}")
                    finally:
                        # Auto-uninstall if we installed it
                        if auto_installed and plugin.audit_uninstall_command():
                            print(f"  [security_audit] Cleaning up {audit_bin}...")
                            try:
                                subprocess.run(
                                    plugin.audit_uninstall_command(), shell=True,
                                    capture_output=True, timeout=60, env=_get_env(),
                                )
                            except Exception:
                                pass
                else:
                    print(f"  [security_audit] {audit_bin} not available, skipping")

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
