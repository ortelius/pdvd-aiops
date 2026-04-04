"""
Rollback node — LLM-powered for error analysis, deterministic for execution.

When tests fail, this node:
1. Uses heuristics to identify the problematic package (high confidence → single rollback)
2. Falls back to LLM for ambiguous cases
3. When confidence is low + many packages remain, uses batch rollback (binary search):
   rolls back the "likely culprit" half to find the failure faster
4. Rolls back identified package(s) via ecosystem plugin
5. Increments retry counter
"""

import json
import os
import re
from typing import Optional

from src.ecosystems import get_plugin_by_name
from src.pipeline.state import PipelineState
from src.utils.env import get_pipeline_env
from src.utils.subprocess import run_cmd


def rollback_node(state: PipelineState) -> dict:
    """
    Analyze test failure, identify problematic package(s), rollback.

    Strategy:
    - High-confidence single match → rollback that one package
    - Low confidence + many remaining → batch rollback (likely-culprit half)
    - No match → LLM analysis → single or batch rollback

    Returns: updated state with rollback applied, retry_count incremented
    """
    repo_path = state["repo_path"]
    package_manager = state["package_manager"]
    test_result = state.get("test_result", {})
    applied_updates = state.get("applied_updates", [])
    dependency_file_name = state.get("dependency_file_name", "")
    retry_count = state.get("retry_count", 0)
    rollback_history = list(state.get("rollback_history", []))
    update_groups = state.get("update_groups")
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("rollback")

    try:
        # Combine error output
        error_output = test_result.get("stderr", "") + "\n" + test_result.get("stdout", "")

        # ── Step 1: Identify problematic package(s) ────────────
        suspected = _heuristic_error_analysis(error_output, applied_updates)

        if not suspected:
            suspected = _llm_error_analysis(error_output, applied_updates, tracker)

        # ── Step 2: Decide rollback strategy ───────────────────
        packages_to_rollback = []

        if suspected and suspected.get("confidence") in ("high", "medium"):
            # High/medium confidence → single package rollback
            packages_to_rollback = [suspected]
        elif len(applied_updates) > 4:
            # Low/no confidence + many packages → batch rollback (binary search)
            packages_to_rollback = _select_batch_for_rollback(
                error_output, applied_updates, update_groups, tracker
            )
        elif suspected:
            # Low confidence but few packages → still try the suspected one
            packages_to_rollback = [suspected]

        if not packages_to_rollback:
            return {
                "retry_count": retry_count + 1,
                "rollback_history": rollback_history,
            }

        # ── Step 3: Execute rollback ───────────────────────────
        plugin = get_plugin_by_name(package_manager)
        if not plugin:
            return {"retry_count": retry_count + 1, "rollback_history": rollback_history}

        rolled_back_names = set()
        for pkg_info in packages_to_rollback:
            pkg_name = pkg_info["package"]
            old_version = pkg_info.get("old_version", "")

            _execute_single_rollback(
                plugin, repo_path, pkg_name, old_version,
                dependency_file_name, tracker,
            )

            rollback_history.append({
                "package": pkg_name,
                "from_version": pkg_info.get("new_version", "?"),
                "to_version": old_version,
            })
            rolled_back_names.add(pkg_name)

        # Run install once after all rollbacks
        build_commands = state.get("build_commands", {})
        if not plugin.rollback_via_command:
            install_cmd = build_commands.get("install") or plugin.default_commands().get("install")
            if install_cmd:
                install_cmd = plugin.fix_command(install_cmd)
                run_cmd(install_cmd, timeout=300, cwd=repo_path, env=get_pipeline_env(repo_path))

        # Remove rolled-back packages from applied_updates
        updated_applied = [u for u in applied_updates if u["name"] not in rolled_back_names]

        batch_msg = f" (batch: {len(rolled_back_names)} packages)" if len(rolled_back_names) > 1 else ""
        print(f"  [rollback] Rolled back: {', '.join(rolled_back_names)}{batch_msg}")

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


def _execute_single_rollback(plugin, repo_path, pkg_name, old_version,
                              dependency_file_name, tracker):
    """Roll back a single package via the ecosystem plugin."""
    if plugin.rollback_via_command:
        cmd = plugin.rollback_command(pkg_name, old_version)
        if cmd:
            cmd = plugin.fix_command(cmd)
            run_cmd(cmd, timeout=120, cwd=repo_path, env=get_pipeline_env(repo_path))
        if tracker:
            tracker.record_tool_call("rollback_command", pkg_name)
    else:
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


def _select_batch_for_rollback(
    error_output: str,
    applied_updates: list[dict],
    update_groups: list[list[dict]] = None,
    tracker=None,
) -> list[dict]:
    """
    Select a batch of packages to rollback for binary-search elimination.

    Strategy:
    1. If update_groups exist, rollback the group most likely to contain the culprit
    2. Otherwise, split applied_updates in half — rollback the "more suspicious" half
       (major bumps + packages mentioned in error)
    """
    # If we have pre-computed groups, use them
    if update_groups and len(update_groups) > 1:
        # Score each group by how suspicious it is
        scored = []
        error_lower = error_output.lower()
        for group in update_groups:
            score = 0
            for pkg in group:
                name = pkg.get("name", "")
                # Mentioned in error = more suspicious
                score += error_lower.count(name.lower()) * 2
                # Major bump = more suspicious
                old = pkg.get("current", "0").lstrip("^~>=v")
                new = pkg.get("latest", "0").lstrip("^~>=v")
                try:
                    if old.split(".")[0] != new.split(".")[0]:
                        score += 3
                except (IndexError, ValueError):
                    pass
            scored.append((group, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        suspect_group = scored[0][0]

        # Convert group format to rollback format
        return [
            {
                "package": p.get("name", ""),
                "old_version": p.get("current", p.get("old", "")),
                "new_version": p.get("latest", p.get("new", "")),
            }
            for p in suspect_group
        ]

    # No groups — split applied_updates into suspicious and safe halves
    error_lower = error_output.lower()

    def suspicion_score(u):
        score = 0
        score += error_lower.count(u["name"].lower()) * 2
        old = u.get("old", "0").lstrip("^~>=v")
        new = u.get("new", "0").lstrip("^~>=v")
        try:
            if old.split(".")[0] != new.split(".")[0]:
                score += 3  # major bump
            elif len(old.split(".")) >= 2 and old.split(".")[1] != new.split(".")[1]:
                score += 1  # minor bump
        except (IndexError, ValueError):
            pass
        return score

    ranked = sorted(applied_updates, key=suspicion_score, reverse=True)
    # Take the more-suspicious half
    half = max(1, len(ranked) // 2)
    suspicious_half = ranked[:half]

    return [
        {
            "package": u["name"],
            "old_version": u.get("old", ""),
            "new_version": u.get("new", ""),
        }
        for u in suspicious_half
    ]


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
    """Use LLM to identify problematic package when heuristics fail."""
    try:
        from src.config.llm import get_llm

        llm = get_llm(temperature=0, max_tokens=200)

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
                    getattr(llm, "model_name", "unknown"),
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
