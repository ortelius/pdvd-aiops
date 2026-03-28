"""
Rollback node — LLM-powered for error analysis, deterministic for execution.

When tests fail, this node:
1. Uses heuristics to identify the problematic package
2. Falls back to LLM (Haiku) for ambiguous cases
3. Rolls back the identified package via ecosystem plugin
4. Increments retry counter
"""

import json
import os
import re
import sys
from typing import Optional

from src.ecosystems import get_plugin_by_name
from src.pipeline.state import PipelineState


def _get_env():
    """Get environment with PATH that includes the current Python's bin directory."""
    env = os.environ.copy()
    python_bin = os.path.dirname(sys.executable)
    if python_bin not in env.get("PATH", ""):
        env["PATH"] = python_bin + os.pathsep + env.get("PATH", "")
    return env


def rollback_node(state: PipelineState) -> dict:
    """
    Analyze test failure, identify problematic package, rollback.

    Returns: updated state with rollback applied, retry_count incremented
    """
    repo_path = state["repo_path"]
    package_manager = state["package_manager"]
    test_result = state.get("test_result", {})
    outdated_packages = state.get("outdated_packages", [])
    applied_updates = state.get("applied_updates", [])
    dependency_file_name = state.get("dependency_file_name", "")
    retry_count = state.get("retry_count", 0)
    rollback_history = list(state.get("rollback_history", []))
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("rollback")

    try:
        # Combine error output
        error_output = test_result.get("stderr", "") + "\n" + test_result.get("stdout", "")

        # ── Step 1: Identify problematic package ─────────────
        suspected = _heuristic_error_analysis(error_output, applied_updates)

        if not suspected:
            suspected = _llm_error_analysis(error_output, applied_updates, tracker)

        if not suspected:
            # Can't identify culprit — go to issue creation
            return {
                "retry_count": retry_count + 1,
                "rollback_history": rollback_history,
            }

        pkg_name = suspected["package"]
        old_version = suspected.get("old_version", "")

        # ── Step 2: Rollback via ecosystem plugin ────────────
        plugin = get_plugin_by_name(package_manager)
        if not plugin:
            return {"retry_count": retry_count + 1, "rollback_history": rollback_history}

        if plugin.rollback_via_command:
            # Command-based rollback (go, etc.)
            import subprocess
            cmd = plugin.rollback_command(pkg_name, old_version)
            if cmd:
                cmd = plugin.fix_command(cmd)
                subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=120, cwd=repo_path, env=_get_env())
            if tracker:
                tracker.record_tool_call("rollback_command", pkg_name)
        else:
            # File-based rollback (npm, pip, cargo, etc.)
            if dependency_file_name:
                dep_path = os.path.join(repo_path, dependency_file_name)
                with open(dep_path, "r") as f:
                    current_content = f.read()

                rolled_back = plugin.rollback_package(
                    current_content, pkg_name, old_version, file_name=dependency_file_name
                )

                with open(dep_path, "w") as f:
                    f.write(rolled_back)

                if tracker:
                    tracker.record_tool_call("rollback_file", pkg_name)

            # Run install after rollback
            import subprocess
            build_commands = state.get("build_commands", {})
            install_cmd = build_commands.get("install") or plugin.default_commands().get("install")
            if install_cmd:
                install_cmd = plugin.fix_command(install_cmd)
                subprocess.run(install_cmd, shell=True, capture_output=True, text=True,
                               timeout=300, cwd=repo_path, env=_get_env())

        # Update rollback history
        rollback_history.append({
            "package": pkg_name,
            "from_version": suspected.get("new_version", "?"),
            "to_version": old_version,
        })

        # Remove rolled-back package from applied_updates
        updated_applied = [u for u in applied_updates if u["name"] != pkg_name]

        return {
            "retry_count": retry_count + 1,
            "rollback_history": rollback_history,
            "applied_updates": updated_applied,
        }

    except Exception as e:
        return {
            "retry_count": retry_count + 1,
            "rollback_history": rollback_history,
        }
    finally:
        if tracker:
            tracker.end_phase()


def _heuristic_error_analysis(error_output: str, applied_updates: list[dict]) -> Optional[dict]:
    """Try to identify the problematic package using heuristics."""
    error_lower = error_output.lower()
    package_names = [u["name"] for u in applied_updates]

    # Count mentions of each package in the error
    matches = []
    for pkg in package_names:
        count = error_lower.count(pkg.lower())
        if count > 0:
            matches.append((pkg, count))

    if matches:
        matches.sort(key=lambda x: x[1], reverse=True)
        pkg_name = matches[0][0]
        update = next((u for u in applied_updates if u["name"] == pkg_name), {})
        return {
            "package": pkg_name,
            "old_version": update.get("old", ""),
            "new_version": update.get("new", ""),
            "confidence": "high" if matches[0][1] >= 3 else "medium",
        }

    # Check for import/require patterns
    import_match = re.search(
        r"(?:from|import|require\()\s*['\"]?([a-zA-Z0-9_@/.-]+)", error_output
    )
    if import_match:
        mentioned = import_match.group(1).split("/")[0].lstrip("@")
        for pkg in package_names:
            if mentioned.lower() in pkg.lower() or pkg.lower() in mentioned.lower():
                update = next((u for u in applied_updates if u["name"] == pkg), {})
                return {
                    "package": pkg,
                    "old_version": update.get("old", ""),
                    "new_version": update.get("new", ""),
                    "confidence": "medium",
                }

    return None


def _llm_error_analysis(error_output: str, applied_updates: list[dict], tracker=None) -> Optional[dict]:
    """Use Haiku to identify problematic package when heuristics fail."""
    try:
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(
            model="claude-haiku-4-5-20251001",
            temperature=0,
            max_tokens=200,
        )

        updates_str = ", ".join(
            f"{u['name']} ({u.get('old', '?')} → {u.get('new', '?')})"
            for u in applied_updates
        )

        prompt = f"""A dependency update broke the tests. Which package most likely caused it?

Updated packages: {updates_str}

Error output (last 1500 chars):
{error_output[-1500:]}

Return ONLY JSON: {{"package": "name", "confidence": "high|medium|low", "reasoning": "brief"}}
If you cannot determine, return: {{"package": null}}"""

        response = llm.invoke(prompt)

        if tracker:
            usage = getattr(response, "usage_metadata", None)
            if usage:
                tracker.record_llm_call(
                    "claude-haiku-4-5-20251001",
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                )

        result = json.loads(response.content.strip())
        if result.get("package"):
            pkg_name = result["package"]
            update = next((u for u in applied_updates if u["name"] == pkg_name), {})
            return {
                "package": pkg_name,
                "old_version": update.get("old", ""),
                "new_version": update.get("new", ""),
                "confidence": result.get("confidence", "low"),
            }

    except Exception:
        pass

    return None
