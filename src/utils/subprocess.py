"""
Safe subprocess execution for pipeline commands.

Centralizes shell safety: uses shlex.split() + shell=False by default,
falls back to shell=True only when shell operators are genuinely needed,
and validates commands against dangerous patterns.
"""

import re
import shlex
import subprocess
from typing import Optional

# Shell operators that require shell=True
_SHELL_OPERATORS = re.compile(r'[;&|<>]|\$\(|`')

# Dangerous patterns that should never appear in automated commands.
# These are checked before ANY command execution (shell or not).
_DANGEROUS_PATTERNS = [
    re.compile(r'\brm\s+(-\w*r\w*\s+)?/'),       # rm -rf / or rm /
    re.compile(r'\bcurl\b.*\|\s*(ba)?sh'),          # curl ... | sh
    re.compile(r'\bwget\b.*\|\s*(ba)?sh'),          # wget ... | sh
    re.compile(r'\bmkfs\b'),                         # mkfs
    re.compile(r'\bdd\b\s+.*of=/dev/'),              # dd of=/dev/
    re.compile(r'>\s*/dev/sd[a-z]'),                 # > /dev/sda
    re.compile(r'\bchmod\b.*777\s+/'),               # chmod 777 /
    re.compile(r'\beval\b'),                          # eval
]


def _needs_shell(cmd: str) -> bool:
    """Check if a command string contains shell operators that require shell=True."""
    return bool(_SHELL_OPERATORS.search(cmd))


def _validate_command(cmd: str) -> None:
    """
    Raise ValueError if the command matches a known dangerous pattern.

    This is a defense-in-depth check — it catches obviously destructive
    commands regardless of their source (plugin, CI config, or LLM).
    """
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(cmd):
            raise ValueError(
                f"Command rejected by safety check: {cmd[:200]}"
            )


def run_cmd(
    cmd: str,
    *,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """
    Execute a command string safely.

    - Validates against dangerous patterns before execution
    - Uses shell=False with shlex.split() for simple commands
    - Falls back to shell=True only when shell operators (&&, |, ;, etc.) are present

    Args:
        cmd: Command string to execute
        cwd: Working directory
        env: Environment variables
        timeout: Timeout in seconds

    Returns:
        subprocess.CompletedProcess

    Raises:
        ValueError: If command matches a dangerous pattern
        subprocess.TimeoutExpired: If command exceeds timeout
    """
    _validate_command(cmd)

    if _needs_shell(cmd):
        return subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )

    args = shlex.split(cmd)
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=env,
    )
