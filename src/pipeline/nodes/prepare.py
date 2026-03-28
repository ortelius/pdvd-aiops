"""
Prepare node — deterministic, 0 LLM tokens.

Applies dependency updates using the ecosystem plugin.
The plugin decides HOW to update (file edit vs command).
The node never checks which package manager it's dealing with.
"""

import os
import subprocess
import sys
from pathlib import Path

from src.ecosystems import get_plugin_by_name
from src.pipeline.state import PipelineState


def _get_env():
    """Get environment with PATH that includes the current Python's bin directory."""
    env = os.environ.copy()
    python_bin = os.path.dirname(sys.executable)
    if python_bin not in env.get("PATH", ""):
        env["PATH"] = python_bin + os.pathsep + env.get("PATH", "")
    return env


def prepare_node(state: PipelineState) -> dict:
    """
    Apply updates via ecosystem plugin, then run install.

    The plugin declares its strategy:
    - updates_via_command=True → run a shell command (go, cargo)
    - updates_via_command=False → edit the dependency file (npm, pip, poetry)
    """
    repo_path = state["repo_path"]
    package_manager = state["package_manager"]
    outdated_packages = state["outdated_packages"]
    build_commands = state.get("build_commands", {})
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("prepare")

    try:
        plugin = get_plugin_by_name(package_manager)
        if not plugin:
            return {"final_status": "error", "final_message": f"No plugin for {package_manager}"}

        if plugin.updates_via_command:
            return _update_via_command(repo_path, plugin, outdated_packages, build_commands, tracker)
        else:
            return _update_via_file(repo_path, plugin, outdated_packages, build_commands, tracker)

    except Exception as e:
        return {"final_status": "error", "final_message": f"Prepare failed: {str(e)}"}
    finally:
        if tracker:
            tracker.end_phase()


def _update_via_command(repo_path, plugin, outdated_packages, build_commands, tracker):
    """Update strategy for ecosystems that use a shell command (go, cargo, etc.)."""
    update_cmd = plugin.update_command(repo_path, outdated_packages)
    if not update_cmd:
        return {"final_status": "up_to_date", "final_message": "No update command available"}

    update_cmd = plugin.fix_command(update_cmd)
    print(f"  [prepare] Running: {update_cmd}")

    result = subprocess.run(
        update_cmd, shell=True, capture_output=True, text=True,
        timeout=300, cwd=repo_path, env=_get_env(),
    )
    if result.returncode != 0:
        print(f"  [prepare] Command failed (exit {result.returncode}): {result.stderr[-300:]}")

    if tracker:
        tracker.record_tool_call("update_command", update_cmd)

    # Run post-update command if any (e.g. go mod tidy)
    post_cmd = plugin.post_update_command()
    if post_cmd:
        post_cmd = plugin.fix_command(post_cmd)
        subprocess.run(
            post_cmd, shell=True, capture_output=True, text=True,
            timeout=120, cwd=repo_path, env=_get_env(),
        )

    # Check if files actually changed
    diff_result = subprocess.run(
        ["git", "diff", "--name-only"], capture_output=True, text=True, cwd=repo_path,
    )
    changed_files = [f.strip() for f in diff_result.stdout.strip().split("\n") if f.strip()]

    if not changed_files:
        print(f"  [prepare] No files changed — dependencies already at latest")
        return {"final_status": "up_to_date", "final_message": "All dependencies are already up to date."}

    # Parse diff to get actual updates
    dep_file = plugin.dependency_file
    diff_content = subprocess.run(
        ["git", "diff", dep_file], capture_output=True, text=True, cwd=repo_path,
    )
    applied = plugin.parse_update_diff(diff_content.stdout, outdated_packages)

    print(f"  [prepare] Modified: {', '.join(changed_files)} ({len(applied)} packages updated)")

    return {
        "dependency_file_name": dep_file,
        "applied_updates": applied,
    }


def _update_via_file(repo_path, plugin, outdated_packages, build_commands, tracker):
    """Update strategy for ecosystems that edit a dependency file (npm, pip, etc.)."""
    # Ask the plugin which file to edit based on what actually exists
    repo_files = {p.name for p in Path(repo_path).rglob("*") if p.is_file()}
    dep_file = plugin.resolve_dependency_file(repo_files)
    dep_path = os.path.join(repo_path, dep_file) if dep_file else ""

    if not dep_file or not os.path.exists(dep_path):
        return {"final_status": "error", "final_message": f"No dependency file found for {plugin.name}"}

    with open(dep_path, "r") as f:
        original_content = f.read()

    if tracker:
        tracker.record_tool_call("read_dependency_file")

    # Apply updates via plugin — pass file_name so it knows the format
    print(f"  [prepare] Applying {len(outdated_packages)} updates to {dep_file}...")
    updated_content, applied = plugin.apply_updates(original_content, outdated_packages, file_name=dep_file)

    if not applied:
        print(f"  [prepare] No updates could be applied to {dep_file}")
        return {"final_status": "up_to_date", "final_message": f"No matching packages found in {dep_file}"}

    print(f"  [prepare] Applied {len(applied)} updates")

    if tracker:
        tracker.record_tool_call("apply_updates")

    # Write updated file
    with open(dep_path, "w") as f:
        f.write(updated_content)

    if tracker:
        tracker.record_tool_call("write_dependency_file")

    # Run install command
    install_cmd = build_commands.get("install") or plugin.default_commands().get("install")
    if install_cmd:
        install_cmd = plugin.fix_command(install_cmd)
        subprocess.run(
            install_cmd, shell=True, capture_output=True, text=True,
            timeout=300, cwd=repo_path, env=_get_env(),
        )
        if tracker:
            tracker.record_tool_call("run_install")

    return {
        "dependency_file_name": dep_file,
        "original_file_content": original_content,
        "updated_file_content": updated_content,
        "applied_updates": applied,
    }
