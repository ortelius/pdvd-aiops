"""
Verification tool registry.

Each verification check is registered with a decorator. The verification agent
node discovers applicable checks dynamically based on what's in the repo.

Adding a new check = one decorated function. No prompt editing, no pipeline changes.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from langchain_core.tools import tool


@dataclass
class VerificationSpec:
    """Metadata for a verification check."""
    tool_func: Any  # The @tool decorated function
    name: str
    description: str
    applies_when: Callable[[str], bool]  # receives repo_path, returns bool


VERIFICATION_REGISTRY: list[VerificationSpec] = []


def register_verification(name: str, description: str, applies_when: Callable[[str], bool]):
    """
    Decorator to register a verification tool.

    Args:
        name: Human-readable check name
        description: What this check does (shown in agent prompt)
        applies_when: Function that takes repo_path and returns True if check applies
    """
    def decorator(func):
        wrapped = tool(func)
        VERIFICATION_REGISTRY.append(
            VerificationSpec(
                tool_func=wrapped,
                name=name,
                description=description,
                applies_when=applies_when,
            )
        )
        return wrapped
    return decorator


def get_applicable_checks(repo_path: str) -> list[VerificationSpec]:
    """Return verification checks applicable to this repo."""
    return [spec for spec in VERIFICATION_REGISTRY if spec.applies_when(repo_path)]


def get_verification_tools(repo_path: str) -> list:
    """Return LangChain tool objects for applicable checks."""
    return [spec.tool_func for spec in get_applicable_checks(repo_path)]


def build_verification_prompt_section(repo_path: str) -> str:
    """Build the dynamic verification section for the agent prompt."""
    applicable = get_applicable_checks(repo_path)
    if not applicable:
        return "No additional verification checks apply to this repository.\n"

    section = "After build+tests pass, run these verification checks:\n"
    for i, spec in enumerate(applicable, 1):
        section += f"{i}. **{spec.name}**: Call `{spec.tool_func.name}`. {spec.description}\n"
    return section


# ── Built-in Verification Checks ─────────────────────────────


@register_verification(
    name="Dockerfile Check",
    description="Read the Dockerfile and check if base image versions or installed tool "
                "versions conflict with the updated dependencies.",
    applies_when=lambda repo_path: os.path.exists(os.path.join(repo_path, "Dockerfile")),
)
def verify_dockerfile(repo_path: str) -> str:
    """
    Read the Dockerfile and report base image, pinned versions, and potential conflicts.

    Args:
        repo_path: Path to the repository

    Returns:
        JSON with Dockerfile analysis
    """
    try:
        dockerfile_path = os.path.join(repo_path, "Dockerfile")
        with open(dockerfile_path, "r") as f:
            content = f.read()

        findings = []

        # Extract FROM lines
        from_lines = re.findall(r'^FROM\s+(.+)$', content, re.MULTILINE)
        for from_line in from_lines:
            findings.append({"type": "base_image", "value": from_line.strip()})

        # Check for pinned versions in RUN commands
        version_pins = re.findall(
            r'(?:apt-get install|apk add|pip install|npm install)\s+.*?([a-zA-Z0-9_-]+=\S+)',
            content
        )
        for pin in version_pins:
            findings.append({"type": "version_pin", "value": pin})

        return json.dumps({
            "status": "success",
            "check": "Dockerfile",
            "findings": findings,
            "raw_content": content[:2000],
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@register_verification(
    name="CI Config Check",
    description="Read GitHub Actions workflows and check if setup steps or version matrices "
                "need updating to match the new dependency versions.",
    applies_when=lambda repo_path: os.path.exists(os.path.join(repo_path, ".github", "workflows")),
)
def verify_ci_config(repo_path: str) -> str:
    """
    Read CI workflow files and report version-related configuration.

    Args:
        repo_path: Path to the repository

    Returns:
        JSON with CI config analysis
    """
    try:
        workflows_dir = Path(repo_path) / ".github" / "workflows"
        findings = []

        for yml_file in workflows_dir.glob("*.yml"):
            content = yml_file.read_text()
            name = yml_file.name

            # Extract version matrices
            version_refs = re.findall(
                r'(?:node-version|python-version|go-version|java-version):\s*["\']?([^\s"\']+)',
                content
            )
            for ver in version_refs:
                findings.append({"file": name, "type": "version_matrix", "value": ver})

            # Extract setup action versions
            setup_actions = re.findall(r'uses:\s*(actions/setup-\S+)', content)
            for action in setup_actions:
                findings.append({"file": name, "type": "setup_action", "value": action})

        return json.dumps({
            "status": "success",
            "check": "CI Config",
            "findings": findings,
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@register_verification(
    name="Docker Compose Check",
    description="Check docker-compose.yml for image versions or build configurations "
                "that may need updating.",
    applies_when=lambda repo_path: (
        os.path.exists(os.path.join(repo_path, "docker-compose.yml"))
        or os.path.exists(os.path.join(repo_path, "docker-compose.yaml"))
        or os.path.exists(os.path.join(repo_path, "compose.yml"))
    ),
)
def verify_docker_compose(repo_path: str) -> str:
    """
    Read docker-compose file and report image versions and build configs.

    Args:
        repo_path: Path to the repository

    Returns:
        JSON with docker-compose analysis
    """
    try:
        compose_file = None
        for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml"]:
            path = os.path.join(repo_path, name)
            if os.path.exists(path):
                compose_file = path
                break

        if not compose_file:
            return json.dumps({"status": "error", "message": "No compose file found"})

        with open(compose_file, "r") as f:
            content = f.read()

        findings = []
        # Extract image references
        images = re.findall(r'image:\s*(\S+)', content)
        for img in images:
            findings.append({"type": "image", "value": img})

        return json.dumps({
            "status": "success",
            "check": "Docker Compose",
            "findings": findings,
            "raw_content": content[:2000],
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
