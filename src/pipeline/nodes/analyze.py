"""
Analyze node — deterministic, 0 LLM tokens.

Clones the repository, detects ecosystem/package manager,
and checks for outdated dependencies.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from src.ecosystems import detect_ecosystem, get_plugin_by_name
from src.pipeline.state import PipelineState
from src.services.cache import get_cache


def _get_env():
    """Get environment with PATH that includes the current Python's bin directory."""
    env = os.environ.copy()
    python_bin = os.path.dirname(sys.executable)
    if python_bin not in env.get("PATH", ""):
        env["PATH"] = python_bin + os.pathsep + env.get("PATH", "")
    return env


def _fix_command(cmd: str, plugin=None) -> str:
    """Let the plugin fix the command for the current system."""
    if plugin:
        return plugin.fix_command(cmd)
    return cmd


def _get_nested(obj, path, default="N/A"):
    """Get nested value from dict using dot notation."""
    for key in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return default
        if obj is None:
            return default
    return obj


def _parse_text_outdated(stdout: str, plugin=None) -> list:
    """Parse text-format outdated output into structured list."""
    # Let plugin handle custom parsing first
    if plugin:
        custom = plugin.parse_outdated_text(stdout)
        if custom:
            return custom

    results = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line or line.startswith(("-", "=", "Package", "Name", "#")):
            continue
        parts = line.split()
        if len(parts) >= 3:
            name = parts[0]
            if all(c in "-|+" for c in name):
                continue
            current = parts[1].strip("()")
            latest = parts[2].strip("()")
            # Some tools have 4+ columns (name current wanted latest)
            if len(parts) >= 4:
                latest = parts[-1].strip("()")
            results.append({"name": name, "current": current, "latest": latest})
    return results


def analyze_node(state: PipelineState) -> dict:
    """
    Clone repo, detect ecosystem, check outdated dependencies.

    Returns state updates:
        repo_path, language, package_manager, detected_info,
        outdated_packages, outdated_count, final_status (if up_to_date)
    """
    repo_url = state["repo_url"]
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("analyze")

    try:
        # ── Step 1: Clone ────────────────────────────────────
        print(f"  [analyze] Cloning {repo_url}...")
        repo_path = _clone_repository(repo_url)
        if tracker:
            tracker.record_tool_call("clone_repository")

        # ── Step 2: Detect ecosystem ─────────────────────────
        repo_files = {p.name for p in Path(repo_path).rglob("*") if p.is_file()}
        plugin = detect_ecosystem(repo_files)

        if not plugin:
            if tracker:
                tracker.end_phase()
            return {
                "repo_path": repo_path,
                "final_status": "error",
                "final_message": "Could not detect package manager for this repository",
            }

        if tracker:
            tracker.record_tool_call("detect_ecosystem")

        # Build detected_info dict (compatible with existing check_outdated logic)
        detected_info = {
            "language": plugin.language,
            "package_manager": plugin.name,
            "outdated_command": plugin.outdated_command(),
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
            "skip_when": plugin.outdated_skip_when(),
        }

        # ── Step 3: Check outdated ───────────────────────────
        print(f"  [analyze] Detected: {plugin.language}/{plugin.name}")
        print(f"  [analyze] Checking outdated packages...")
        outdated = _check_outdated(repo_path, repo_url, plugin, detected_info)
        if tracker:
            tracker.record_tool_call("check_outdated")

        updates = {
            "repo_path": repo_path,
            "language": plugin.language,
            "package_manager": plugin.name,
            "detected_info": detected_info,
            "outdated_packages": outdated,
            "outdated_count": len(outdated),
        }

        if len(outdated) == 0:
            updates["final_status"] = "up_to_date"
            updates["final_message"] = "All dependencies are up to date."
            print(f"  [analyze] All dependencies are up to date!")
        else:
            print(f"  [analyze] Found {len(outdated)} outdated packages")

        return updates

    except Exception as e:
        return {
            "final_status": "error",
            "final_message": f"Analysis failed: {str(e)}",
        }
    finally:
        if tracker:
            tracker.end_phase()


def _clone_repository(repo_url: str) -> str:
    """Clone repo with cache support. Returns repo_path."""
    cache = get_cache()

    cached_path = cache.get_cached_repository(repo_url)
    if cached_path:
        temp_dir = tempfile.mkdtemp(prefix="dep_analyzer_")
        shutil.copytree(cached_path, temp_dir, dirs_exist_ok=True)
        return temp_dir

    temp_dir = tempfile.mkdtemp(prefix="dep_analyzer_")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, temp_dir],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to clone: {result.stderr}")

    try:
        cache.cache_repository(repo_url, temp_dir)
    except Exception:
        pass  # don't fail if caching fails

    return temp_dir


def _check_outdated(repo_path: str, repo_url: str, plugin, detected_info: dict) -> list[dict]:
    """Run the outdated command and parse output. Returns list of outdated packages."""
    cache = get_cache()

    # Check cache
    cached = cache.get_cached_outdated(repo_url)
    if cached:
        return cached.get("outdated_packages", [])

    outdated_cmd = detected_info.get("outdated_command")
    if not outdated_cmd:
        return []

    outdated_cmd = _fix_command(outdated_cmd, plugin)

    result = subprocess.run(
        outdated_cmd.split(), capture_output=True, text=True,
        timeout=120, cwd=repo_path, env=_get_env(),
    )

    stdout = result.stdout.strip()
    if not stdout:
        return []

    outdated_list = []
    output_format = detected_info.get("output_format", "text")
    field_map = detected_info.get("field_map", {})

    try:
        if output_format == "json_dict":
            data = json.loads(stdout)
            for pkg_key, info in data.items():
                name_field = field_map.get("name", "name")
                outdated_list.append({
                    "name": pkg_key if name_field == "_key" else info.get(name_field, pkg_key),
                    "current": info.get(field_map.get("current", "current"), "N/A"),
                    "latest": info.get(field_map.get("latest", "latest"), "N/A"),
                })

        elif output_format == "json_array":
            data = json.loads(stdout)
            for item in data:
                outdated_list.append({
                    "name": item.get(field_map.get("name", "name"), "N/A"),
                    "current": item.get(field_map.get("current", "current"), "N/A"),
                    "latest": item.get(field_map.get("latest", "latest"), "N/A"),
                })

        elif output_format == "ndjson":
            skip_when = detected_info.get("skip_when", {})
            decoder = json.JSONDecoder()
            pos = 0
            while pos < len(stdout):
                while pos < len(stdout) and stdout[pos] in " \t\n\r":
                    pos += 1
                if pos >= len(stdout):
                    break
                try:
                    obj, end_pos = decoder.raw_decode(stdout, pos)
                    pos = end_pos
                except json.JSONDecodeError:
                    break

                skip = False
                for key, val in skip_when.items():
                    if val is None and key not in obj:
                        skip = True
                    elif val is not None and obj.get(key) == val:
                        skip = True
                if skip:
                    continue

                outdated_list.append({
                    "name": _get_nested(obj, field_map.get("name", "name")),
                    "current": _get_nested(obj, field_map.get("current", "current")),
                    "latest": _get_nested(obj, field_map.get("latest", "latest")),
                })

        else:  # text format
            outdated_list = _parse_text_outdated(stdout, plugin)

    except json.JSONDecodeError:
        outdated_list = _parse_text_outdated(stdout, plugin)

    # Cache results
    try:
        cache.cache_outdated(repo_url, {
            "status": "success",
            "outdated_packages": outdated_list,
            "outdated_count": len(outdated_list),
        })
    except Exception:
        pass

    return outdated_list
