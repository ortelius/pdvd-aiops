"""
Analyze node — deterministic, 0 LLM tokens.

Clones the repository, detects ecosystem/package manager,
and checks for outdated dependencies.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from src.ecosystems import detect_ecosystem, get_plugin_by_name
from src.pipeline.state import PipelineState
from src.services.cache import get_cache
from src.utils.env import get_pipeline_env
from src.utils.subprocess import run_cmd


def _fix_command(cmd: str, plugin=None, repo_path: str = "") -> str:
    """Let the plugin fix the command for the current system."""
    if plugin:
        # Pass repo_path so pip plugin can use venv python
        try:
            return plugin.fix_command(cmd, repo_path=repo_path)
        except TypeError:
            # Plugins that don't accept repo_path
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


def parse_outdated_output(stdout: str, detected_info: dict, plugin=None) -> list[dict]:
    """
    Parse raw outdated command output into a list of {name, current, latest} dicts.

    This function handles all output formats (json_dict, json_array, ndjson, text)
    based on the plugin's declared output_format and field_map.

    Extracted as a standalone function for testability.
    """
    output_format = detected_info.get("output_format", "text")
    field_map = detected_info.get("field_map", {})
    outdated_list = []

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

    return outdated_list


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
        # Only scan root-level files for detection — dependency manifests
        # (requirements.txt, go.mod, package.json, etc.) are always at the root.
        # rglob would pick up files in subdirectories (e.g. test fixtures).
        repo_root = Path(repo_path)
        repo_files = {p.name for p in repo_root.iterdir() if p.is_file()}
        # Also include .github/ files for CI detection
        github_dir = repo_root / ".github"
        if github_dir.exists():
            for p in github_dir.rglob("*"):
                if p.is_file():
                    repo_files.add(p.name)
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

        # ── Step 2.5: Set up environment (venv for Python, etc.) ──
        print(f"  [analyze] Detected: {plugin.language}/{plugin.name}")
        repo_python = plugin.setup_environment(repo_path)
        if tracker and repo_python:
            tracker.record_tool_call("setup_environment")

        # ── Step 3: Check outdated ───────────────────────────
        print(f"  [analyze] Checking outdated packages...")
        outdated = _check_outdated(repo_path, repo_url, plugin, detected_info)
        if tracker:
            tracker.record_tool_call("check_outdated")

        # Fill in missing "current" versions from the dependency file.
        # Some tools (e.g. npm outdated) report current as empty when a package
        # isn't installed in node_modules, even though it's pinned in package.json.
        outdated = _fill_missing_versions(outdated, repo_path, plugin, repo_files)

        # Filter out packages where current == latest (not actually outdated).
        # This happens when npm outdated reports packages where "wanted" differs
        # from "current" but "latest" is already installed.
        before_count = len(outdated)
        outdated = [
            p for p in outdated
            if p.get("current", "").lstrip("^~>=v") != p.get("latest", "").lstrip("^~>=v")
        ]
        if len(outdated) < before_count:
            print(f"  [analyze] Filtered {before_count - len(outdated)} already-up-to-date package(s)")

        updates = {
            "repo_path": repo_path,
            "repo_python": repo_python,
            "language": plugin.language,
            "package_manager": plugin.name,
            "detected_info": detected_info,
            "outdated_packages": outdated,
            "outdated_count": len(outdated),
        }

        if len(outdated) == 0 and not plugin.updates_via_command:
            # For file-based ecosystems: no outdated = nothing to do
            updates["final_status"] = "up_to_date"
            updates["final_message"] = "All dependencies are up to date."
            print(f"  [analyze] All dependencies are up to date!")
        elif len(outdated) == 0 and plugin.updates_via_command:
            # For command-based ecosystems (go, cargo): the outdated check
            # may miss updates that `go get -u` / `cargo update` would find.
            # Let the prepare node run the update command and check for changes.
            print(f"  [analyze] Outdated check found 0, but will run update command to verify")
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


def _fill_missing_versions(
    outdated: list[dict], repo_path: str, plugin, repo_files: set[str]
) -> list[dict]:
    """
    Fill in missing 'current' versions from the dependency file.

    When a tool like `npm outdated` reports current as empty/N/A (e.g. package
    not installed in node_modules), we read the actual pinned version from the
    dependency file (package.json, requirements.txt, etc.).
    """
    missing = [p for p in outdated if not p.get("current") or p["current"] == "N/A"]
    if not missing:
        return outdated

    # Read and parse the dependency file
    dep_file = plugin.resolve_dependency_file(repo_files)
    if not dep_file:
        return outdated

    dep_path = os.path.join(repo_path, dep_file)
    if not os.path.isfile(dep_path):
        return outdated

    try:
        with open(dep_path, "r") as f:
            content = f.read()
        deps = plugin.parse_dependencies(content)
        version_map = {d.name: d.current for d in deps if d.current}
    except Exception:
        return outdated

    # Fill in missing versions
    filled = 0
    for pkg in outdated:
        if not pkg.get("current") or pkg["current"] == "N/A":
            pinned = version_map.get(pkg["name"], "")
            if pinned:
                pkg["current"] = pinned
                filled += 1

    if filled:
        print(f"  [analyze] Filled {filled} missing version(s) from {dep_file}")

    return outdated


def _clone_repository(repo_url: str) -> str:
    """Clone repo fresh every time. Returns repo_path.

    We always clone fresh because the pipeline modifies the repo
    (applies updates, runs go get -u, etc.). Using a cached clone
    would mean diffing against previously-modified files instead of
    the real upstream, causing wrong version numbers in PRs.
    """
    temp_dir = tempfile.mkdtemp(prefix="dep_analyzer_")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, temp_dir],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to clone: {result.stderr}")

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

    outdated_cmd = _fix_command(outdated_cmd, plugin, repo_path)

    result = run_cmd(
        outdated_cmd, timeout=120, cwd=repo_path, env=get_pipeline_env(repo_path),
    )

    stdout = result.stdout.strip()
    if not stdout:
        return []

    outdated_list = parse_outdated_output(stdout, detected_info, plugin)

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
