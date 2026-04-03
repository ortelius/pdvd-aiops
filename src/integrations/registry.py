"""
Integration tool registry.

Generic registry for DevOps/DevSecOps tools that can be:
- Detected by config file presence in a repository
- Run locally without API keys
- Parsed for structured output

Adding a new tool = one @register_integration call in a definition module.
Pipeline nodes never name specific tools — they iterate over the registry.
"""

import importlib
import os
import pkgutil
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from src.utils.subprocess import run_cmd


@dataclass
class IntegrationDef:
    """Definition of a detectable/runnable integration tool."""

    name: str  # e.g. "eslint", "trivy", "renovate"
    category: str  # "linter", "formatter", "security_scanner", "dependency_manager"
    config_files: list[str]  # files whose presence indicates this tool is configured
    run_command: str  # command to execute (may contain {repo_path} placeholder)
    detect_command: Optional[str] = None  # command to check if tool is installed (e.g. "eslint --version")
    install_command: Optional[str] = None  # command to install the tool if missing
    uninstall_command: Optional[str] = None  # command to clean up after running
    output_format: str = "text"  # "text", "json", "sarif"
    parse_output: Optional[Callable[[str, str], list[dict]]] = None  # (stdout, stderr) -> findings
    ecosystem: Optional[str] = None  # "nodejs", "python", "go", "rust", None = universal
    severity: str = "warning"  # "info", "warning", "error" — how to treat failures
    description: str = ""


# ── Registry ──────────────────────────────────────────────

_registry: list[IntegrationDef] = []


def register_integration(
    name: str,
    category: str,
    config_files: list[str],
    run_command: str,
    detect_command: Optional[str] = None,
    install_command: Optional[str] = None,
    uninstall_command: Optional[str] = None,
    output_format: str = "text",
    parse_output: Optional[Callable] = None,
    ecosystem: Optional[str] = None,
    severity: str = "warning",
    description: str = "",
) -> IntegrationDef:
    """Register an integration tool definition. Returns the created IntegrationDef."""
    defn = IntegrationDef(
        name=name,
        category=category,
        config_files=config_files,
        run_command=run_command,
        detect_command=detect_command,
        install_command=install_command,
        uninstall_command=uninstall_command,
        output_format=output_format,
        parse_output=parse_output,
        ecosystem=ecosystem,
        severity=severity,
        description=description,
    )
    # Avoid duplicates on re-import
    if not any(d.name == name for d in _registry):
        _registry.append(defn)
    return defn


def get_all_integrations() -> list[IntegrationDef]:
    """Return all registered integration definitions."""
    return list(_registry)


def get_integrations_by_category(category: str) -> list[IntegrationDef]:
    """Return integrations filtered by category."""
    return [d for d in _registry if d.category == category]


# ── Detection ─────────────────────────────────────────────


def detect_integrations(
    repo_path: str, ecosystem: Optional[str] = None
) -> list[dict]:
    """
    Scan a cloned repo for configured integration tools.

    Returns list of dicts with integration info + detected config file path.
    Filters by ecosystem if provided (also includes universal tools).
    """
    repo = Path(repo_path)
    repo_files = {
        str(p.relative_to(repo))
        for p in repo.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }
    # Also collect just filenames for simple matches
    repo_filenames = {Path(f).name for f in repo_files}

    detected = []
    for defn in _registry:
        # Filter by ecosystem: include if universal or matching
        if defn.ecosystem and ecosystem and defn.ecosystem != ecosystem:
            continue

        # Check if any config file exists
        matched_config = None
        for cfg in defn.config_files:
            if "*" in cfg:
                # Glob pattern (e.g. ".eslintrc*")
                pattern = cfg.replace("*", "")
                if any(f.startswith(pattern) or f.endswith(pattern) for f in repo_filenames):
                    matched_config = cfg
                    break
            elif "/" in cfg:
                # Path pattern (e.g. ".github/dependabot.yml")
                if cfg in repo_files:
                    matched_config = cfg
                    break
            else:
                # Simple filename
                if cfg in repo_filenames:
                    matched_config = cfg
                    break

        if matched_config:
            detected.append({
                "name": defn.name,
                "category": defn.category,
                "config_file": matched_config,
                "run_command": defn.run_command,
                "detect_command": defn.detect_command,
                "install_command": defn.install_command,
                "uninstall_command": defn.uninstall_command,
                "output_format": defn.output_format,
                "severity": defn.severity,
                "description": defn.description,
                "ecosystem": defn.ecosystem,
            })

    return detected


def _tool_installed(detect_command: Optional[str]) -> bool:
    """Check if a tool is installed by running its detect command."""
    if not detect_command:
        return True
    cmd_parts = detect_command.split()
    env = _get_integration_env()
    # Check PATH including GOPATH/bin, ~/.cargo/bin etc.
    path_dirs = env.get("PATH", "").split(os.pathsep)
    found = shutil.which(cmd_parts[0], path=os.pathsep.join(path_dirs))
    if not found:
        return False
    try:
        subprocess.run(
            cmd_parts, capture_output=True, timeout=10, env=env,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def get_runnable_integrations(
    repo_path: str, ecosystem: Optional[str] = None
) -> list[dict]:
    """
    Detect integrations AND verify the tool binary is available.
    Returns only integrations that can actually be executed.
    """
    detected = detect_integrations(repo_path, ecosystem)
    runnable = []
    for info in detected:
        if _tool_installed(info.get("detect_command")):
            info["runnable"] = True
            runnable.append(info)
        else:
            # Still include as detected but not runnable
            info["runnable"] = False
            runnable.append(info)
    return runnable


def _get_integration_env() -> dict:
    """Build environment for integration tool execution with all tool bin dirs on PATH."""
    env = os.environ.copy()
    extra_paths = []

    # Go bin (GOPATH/bin)
    gopath = env.get("GOPATH") or os.path.expanduser("~/go")
    gobin = os.path.join(gopath, "bin")
    if gobin not in env.get("PATH", ""):
        extra_paths.append(gobin)

    # Cargo bin (~/.cargo/bin)
    cargo_bin = os.path.expanduser("~/.cargo/bin")
    if os.path.isdir(cargo_bin) and cargo_bin not in env.get("PATH", ""):
        extra_paths.append(cargo_bin)

    # Python bin
    import sys
    python_bin = os.path.dirname(sys.executable)
    if python_bin not in env.get("PATH", ""):
        extra_paths.append(python_bin)

    if extra_paths:
        env["PATH"] = os.pathsep.join(extra_paths) + os.pathsep + env.get("PATH", "")

    # Renovate expects GITHUB_TOKEN
    if not env.get("GITHUB_TOKEN") and env.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
        env["GITHUB_TOKEN"] = env["GITHUB_PERSONAL_ACCESS_TOKEN"]

    return env


def _auto_install(integration: dict) -> bool:
    """Install a tool if install_command is defined. Returns True if installed."""
    install_cmd = integration.get("install_command")
    if not install_cmd:
        return False
    try:
        print(f"  [auto-install] Installing {integration['name']}...")
        result = run_cmd(
            install_cmd, timeout=120, env=_get_integration_env(),
        )
        if result.returncode == 0:
            print(f"  [auto-install] {integration['name']} installed successfully")
            return True
        else:
            print(f"  [auto-install] {integration['name']} install failed: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"  [auto-install] {integration['name']} install error: {e}")
        return False


def _auto_uninstall(integration: dict):
    """Uninstall a tool if uninstall_command is defined."""
    uninstall_cmd = integration.get("uninstall_command")
    if not uninstall_cmd:
        return
    try:
        print(f"  [auto-install] Cleaning up {integration['name']}...")
        run_cmd(
            uninstall_cmd, timeout=60, env=_get_integration_env(),
        )
    except Exception:
        pass  # best-effort cleanup


def run_integration(
    repo_path: str, integration: dict, timeout: int = 180
) -> dict:
    """
    Execute a single integration tool against a repo.

    If the tool is not installed but has an install_command, it will be
    auto-installed before running and auto-uninstalled after.

    Returns: {name, status, exit_code, stdout, stderr, findings}
    """
    name = integration["name"]
    cmd = integration["run_command"].replace("{repo_path}", repo_path)
    auto_installed = False

    try:
        # Auto-install if tool is not available but has an install command
        if not integration.get("runnable", True) and integration.get("install_command"):
            auto_installed = _auto_install(integration)
            if not auto_installed:
                return {
                    "name": name,
                    "category": integration.get("category", ""),
                    "status": "error",
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"Auto-install failed for {name}",
                    "findings": [],
                }

        result = run_cmd(
            cmd, timeout=timeout, cwd=repo_path, env=_get_integration_env(),
        )

        # Parse output if a parser is registered
        findings = []
        defn = next((d for d in _registry if d.name == name), None)
        if defn and defn.parse_output:
            try:
                findings = defn.parse_output(result.stdout, result.stderr)
            except Exception:
                pass

        # Determine status based on exit code and severity
        if result.returncode == 0:
            status = "pass"
        else:
            status = integration.get("severity", "warning")

        return {
            "name": name,
            "category": integration.get("category", ""),
            "status": status,
            "exit_code": result.returncode,
            "stdout": result.stdout[-3000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
            "findings": findings,
        }

    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "category": integration.get("category", ""),
            "status": "error",
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Timed out after {timeout}s",
            "findings": [],
        }
    except Exception as e:
        return {
            "name": name,
            "category": integration.get("category", ""),
            "status": "error",
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "findings": [],
        }
    finally:
        if auto_installed:
            _auto_uninstall(integration)


# ── Auto-discover and import all definition modules ───────


def _load_definitions():
    """Import all modules in src/integrations/definitions/ to trigger registrations."""
    defs_dir = Path(__file__).parent / "definitions"
    if not defs_dir.exists():
        return
    for _importer, modname, _ispkg in pkgutil.iter_modules([str(defs_dir)]):
        if modname.startswith("_"):
            continue
        importlib.import_module(f"src.integrations.definitions.{modname}")


_load_definitions()
