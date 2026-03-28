"""Docker and container runtime utilities."""

import os
import shutil
import subprocess
from typing import Optional


def get_docker_path() -> str:
    """Get the absolute path to the docker executable.

    Using absolute paths prevents PyCharm debugger issues where it
    tries to check if 'docker' is a Python script.
    """
    docker_path = shutil.which("docker")
    if docker_path:
        return docker_path

    # Common Docker paths on different systems
    common_paths = [
        "/usr/local/bin/docker",
        "/usr/bin/docker",
        "/opt/homebrew/bin/docker",
        "/Applications/Docker.app/Contents/Resources/bin/docker",
    ]

    for path in common_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    return "docker"  # Fallback to PATH lookup


def find_command_path(command: str) -> Optional[str]:
    """
    Find the full path to a command, checking common locations.

    Args:
        command: Command name to find (e.g., 'docker', 'podman')

    Returns:
        Full path to command if found, None otherwise
    """
    # First try using shutil.which (respects PATH)
    cmd_path = shutil.which(command)
    if cmd_path:
        return cmd_path

    # Common installation paths for macOS
    common_paths_mac = [
        f"/usr/local/bin/{command}",
        f"/opt/homebrew/bin/{command}",
        f"/usr/bin/{command}",
        f"/opt/local/bin/{command}",
        # OrbStack specific paths
        f"/Applications/OrbStack.app/Contents/MacOS/{command}",
        f"~/.orbstack/bin/{command}",
    ]

    # Common installation paths for Linux
    common_paths_linux = [
        f"/usr/local/bin/{command}",
        f"/usr/bin/{command}",
        f"/bin/{command}",
        f"/snap/bin/{command}",
        f"~/.local/bin/{command}",
    ]

    # Try macOS paths first, then Linux
    all_paths = common_paths_mac + common_paths_linux

    # Expand ~ and check if file exists
    for path in all_paths:
        expanded_path = os.path.expanduser(path)
        if os.path.isfile(expanded_path) and os.access(expanded_path, os.X_OK):
            return expanded_path

    return None


def detect_container_runtime() -> str:
    """
    Auto-detect available container runtime.

    Checks for container runtimes in order of preference:
    1. docker (Docker Desktop, OrbStack, Rancher Desktop)
    2. podman (Podman Desktop, native Podman)
    3. nerdctl (containerd with nerdctl)

    Returns:
        Full path or name of the detected container runtime command

    Raises:
        RuntimeError: If no container runtime is found
    """
    runtimes = ["docker", "podman", "nerdctl"]

    for runtime in runtimes:
        # Try to find the command path
        cmd_path = find_command_path(runtime)

        if cmd_path:
            # Verify it works by running --version
            try:
                result = subprocess.run(
                    [cmd_path, "--version"], capture_output=True, timeout=5, text=True
                )
                if result.returncode == 0:
                    # Always return the full path to avoid PATH issues
                    # (especially important for PyCharm debugger and subprocess calls)
                    return cmd_path
            except (subprocess.TimeoutExpired, Exception):
                continue

    # Provide helpful error message
    error_msg = (
        "No container runtime found. Please ensure one of the following is installed:\n"
        "  - Docker Desktop: https://www.docker.com/products/docker-desktop\n"
        "  - OrbStack (macOS): https://orbstack.dev/\n"
        "  - Podman Desktop: https://podman-desktop.io/\n"
        "  - Rancher Desktop: https://rancherdesktop.io/\n\n"
        "If you have OrbStack or Docker installed:\n"
        "1. Ensure it's running (open the application)\n"
        "2. Verify with: docker --version (in terminal)\n"
        "3. If terminal works but Python doesn't, the issue is PATH.\n\n"
        "For macOS users, add docker to PATH:\n"
        "  echo 'export PATH=\"/usr/local/bin:/opt/homebrew/bin:$PATH\"' >> ~/.zshrc\n"
        "  source ~/.zshrc\n\n"
        "Checked locations:\n"
        "  - $PATH (using shutil.which)\n"
        "  - /usr/local/bin/docker\n"
        "  - /opt/homebrew/bin/docker\n"
        "  - /usr/bin/docker\n"
        "  - ~/.orbstack/bin/docker\n"
        "  - /Applications/OrbStack.app/Contents/MacOS/docker"
    )
    raise RuntimeError(error_msg)
