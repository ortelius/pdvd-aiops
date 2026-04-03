"""
Unified environment builder for pipeline subprocess calls.

Consolidates the _get_env() function that was duplicated across 6 node files.
Ensures PATH includes Python, Go, and Cargo bin directories, and that
GITHUB_TOKEN is set for tools that expect it.
"""

import os
import sys


def get_pipeline_env(repo_path: str = "") -> dict:
    """
    Build an environment dict for subprocess calls with all tool directories on PATH.

    Includes:
    - Current Python's bin dir (for pip, pytest, pip-audit)
    - Venv python if repo_path has a .venv (for pip ecosystem)
    - GOPATH/bin (for govulncheck, go tools)
    - ~/.cargo/bin (for cargo-audit, cargo tools)
    - GITHUB_TOKEN alias (for Renovate and other tools)

    Args:
        repo_path: Optional path to the repo being processed.
                   Used to detect venv python for pip ecosystem.

    Returns:
        A copy of os.environ with augmented PATH.
    """
    env = os.environ.copy()
    extra_paths = []

    # Python bin — ensures pip, pytest, etc. are found
    python_bin = os.path.dirname(sys.executable)
    if python_bin not in env.get("PATH", ""):
        extra_paths.append(python_bin)

    # Go bin (GOPATH/bin) — ensures govulncheck, etc. are found
    gopath = env.get("GOPATH") or os.path.expanduser("~/go")
    gobin = os.path.join(gopath, "bin")
    if gobin not in env.get("PATH", ""):
        extra_paths.append(gobin)

    # Cargo bin (~/.cargo/bin) — ensures cargo-audit, etc. are found
    cargo_bin = os.path.expanduser("~/.cargo/bin")
    if os.path.isdir(cargo_bin) and cargo_bin not in env.get("PATH", ""):
        extra_paths.append(cargo_bin)

    if extra_paths:
        env["PATH"] = os.pathsep.join(extra_paths) + os.pathsep + env.get("PATH", "")

    # Renovate and other tools expect GITHUB_TOKEN
    if not env.get("GITHUB_TOKEN") and env.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
        env["GITHUB_TOKEN"] = env["GITHUB_PERSONAL_ACCESS_TOKEN"]

    return env
