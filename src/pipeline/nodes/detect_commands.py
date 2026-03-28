"""
Detect Commands node — parses CI config, falls back to single Haiku LLM call.

Strategy:
1. Parse .github/workflows/*.yml to extract build/test commands (0 tokens)
2. If CI config found → use those commands
3. If not → single Haiku call with repo evidence (~$0.003)
4. Fallback → ecosystem plugin defaults
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

from src.ecosystems import get_plugin_by_name
from src.pipeline.state import PipelineState


def detect_commands_node(state: PipelineState) -> dict:
    """
    Detect build/test/install commands for the repository.

    Returns: build_commands, commands_source
    """
    repo_path = state["repo_path"]
    package_manager = state["package_manager"]
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("detect_commands")

    try:
        # Strategy 1: Parse CI config (free)
        commands = _parse_ci_config(repo_path, package_manager)
        if commands and (commands.get("build") or commands.get("test")):
            if tracker:
                tracker.record_tool_call("parse_ci_config")
            print(f"  [detect] Commands from CI config: build={commands.get('build')}, test={commands.get('test')}")
            return {"build_commands": commands, "commands_source": "ci_config"}

        # Strategy 2: Parse package.json scripts (for JS ecosystems)
        if os.path.exists(os.path.join(repo_path, "package.json")):
            commands = _parse_package_json_scripts(repo_path, package_manager)
            if commands and (commands.get("build") or commands.get("test")):
                if tracker:
                    tracker.record_tool_call("parse_package_json")
                return {"build_commands": commands, "commands_source": "package_json"}

        # Strategy 3: Single Haiku LLM call with repo evidence
        commands = _llm_detect_commands(repo_path, package_manager, tracker)
        if commands and (commands.get("build") or commands.get("test")):
            return {"build_commands": commands, "commands_source": "haiku_llm"}

        # Strategy 4: Ecosystem plugin defaults
        plugin = get_plugin_by_name(package_manager)
        if plugin:
            commands = plugin.default_commands()
            if tracker:
                tracker.record_tool_call("ecosystem_defaults")
            return {"build_commands": commands, "commands_source": "ecosystem_default"}

        return {
            "build_commands": {"install": None, "build": None, "test": None, "lint": None},
            "commands_source": "none",
        }

    except Exception as e:
        # Fallback to plugin defaults on any error
        plugin = get_plugin_by_name(package_manager)
        commands = plugin.default_commands() if plugin else {}
        return {"build_commands": commands, "commands_source": "fallback"}
    finally:
        if tracker:
            tracker.end_phase()


def _parse_ci_config(repo_path: str, package_manager: str) -> Optional[dict]:
    """Parse GitHub Actions workflows to extract build/test commands."""
    workflows_dir = Path(repo_path) / ".github" / "workflows"
    if not workflows_dir.exists():
        return None

    commands = {"install": None, "build": None, "test": None, "lint": None}

    # Get patterns from the plugin (ecosystem-specific) + generic fallbacks
    plugin = get_plugin_by_name(package_manager)
    build_patterns = (plugin.ci_build_patterns() if plugin else []) + [r'make build']
    test_patterns = (plugin.ci_test_patterns() if plugin else []) + [r'make test']
    install_patterns = (plugin.ci_install_patterns() if plugin else [])

    # If plugin provides no patterns, use generic ones
    if not build_patterns:
        build_patterns = [r'build', r'compile']
    if not test_patterns:
        test_patterns = [r'test']

    for yml_file in workflows_dir.glob("*.yml"):
        try:
            content = yml_file.read_text()
        except Exception:
            continue

        # Extract all `run:` lines
        run_lines = re.findall(r'run:\s*[|]?\s*\n?\s*(.+)', content)
        run_lines += re.findall(r'run:\s+(\S.*)', content)

        for line in run_lines:
            line = line.strip()

            if not commands["install"]:
                for pattern in install_patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        commands["install"] = line
                        break

            if not commands["build"]:
                for pattern in build_patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        commands["build"] = line
                        break

            if not commands["test"]:
                for pattern in test_patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        commands["test"] = line
                        break

    return commands if any(commands.values()) else None


def _parse_package_json_scripts(repo_path: str, package_manager: str) -> Optional[dict]:
    """Parse package.json scripts section for JS projects."""
    package_json_path = os.path.join(repo_path, "package.json")
    if not os.path.exists(package_json_path):
        return None

    try:
        with open(package_json_path, "r") as f:
            data = json.load(f)
        scripts = data.get("scripts", {})

        pm = package_manager
        commands = {
            "install": f"{pm} install",
            "build": f"{pm} run build" if "build" in scripts else None,
            "test": f"{pm} test" if "test" in scripts else None,
            "lint": f"{pm} run lint" if "lint" in scripts else None,
        }
        return commands
    except Exception:
        return None


def _llm_detect_commands(
    repo_path: str, package_manager: str, tracker=None
) -> Optional[dict]:
    """
    Use a single Haiku LLM call to detect build/test commands.
    Gathers repo evidence and asks the model to identify commands.
    Cost: ~$0.003
    """
    try:
        from langchain_anthropic import ChatAnthropic

        # Gather evidence (file reads only, no tokens yet)
        evidence = _gather_repo_evidence(repo_path)
        if not evidence:
            return None

        llm = ChatAnthropic(
            model="claude-haiku-4-5-20251001",
            temperature=0,
            max_tokens=300,
        )

        prompt = f"""Given this repository structure and configuration files, identify the commands to:
1. Install dependencies
2. Build the project
3. Run tests
4. Run linting (if available)

Package manager: {package_manager}

Repository evidence:
{json.dumps(evidence, indent=2)}

Return ONLY a JSON object with these keys: install, build, test, lint
Use null for any command you cannot determine. No explanation."""

        response = llm.invoke(prompt)

        # Track the LLM call
        if tracker:
            usage = getattr(response, "usage_metadata", None)
            if usage:
                tracker.record_llm_call(
                    "claude-haiku-4-5-20251001",
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                )

        # Parse response
        content = response.content.strip()
        # Extract JSON from response (handle markdown code blocks)
        if "```" in content:
            content = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            content = content.group(1) if content else response.content.strip()

        return json.loads(content)

    except Exception:
        return None


def _gather_repo_evidence(repo_path: str) -> dict:
    """Gather config files for the LLM to analyze."""
    evidence = {}

    # File tree (top 2 levels, max 50 entries)
    entries = []
    root = Path(repo_path)
    for item in sorted(root.iterdir()):
        if item.name.startswith(".") and item.name not in (".github",):
            continue
        entries.append(item.name + ("/" if item.is_dir() else ""))
        if item.is_dir() and len(entries) < 40:
            try:
                for child in sorted(item.iterdir())[:10]:
                    entries.append(f"  {child.name}")
            except PermissionError:
                pass
    evidence["file_tree"] = "\n".join(entries[:50])

    # Config files that define build commands
    config_files = [
        "Makefile", "Justfile", "Taskfile.yml",
        "package.json", "pyproject.toml", "tox.ini",
        "Cargo.toml", "docker-compose.yml",
        "Rakefile", "build.gradle", "pom.xml",
    ]

    for name in config_files:
        path = os.path.join(repo_path, name)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    content = f.read()
                evidence[name] = content[:1500]  # Truncate
            except Exception:
                pass

    # CI configs
    workflows_dir = Path(repo_path) / ".github" / "workflows"
    if workflows_dir.exists():
        for yml in list(workflows_dir.glob("*.yml"))[:3]:
            try:
                evidence[f".github/workflows/{yml.name}"] = yml.read_text()[:1500]
            except Exception:
                pass

    return evidence
