"""
Orchestrator router node — LLM-powered (Haiku).

Routes incoming tasks to the appropriate pipeline path.
Today: always routes to "dependency_update".
Future: routes to security_scan, migration, dockerfile_optimize, etc.
"""

import json
import os
from typing import Optional

from src.config.llm import get_llm
from src.pipeline.state import PipelineState


# Available task routes — extend this as you add new capabilities
AVAILABLE_ROUTES = {
    "dependency_update": "Analyze and update outdated dependencies, test changes, create PR or Issue",
    # Future:
    # "security_scan": "Scan dependencies for known vulnerabilities",
    # "dockerfile_optimize": "Optimize Dockerfile for size and security",
    # "migrate_framework": "Migrate between framework versions",
}


def orchestrator_node(state: PipelineState) -> dict:
    """
    Route the task to the appropriate pipeline path.

    For now, with only one route, this uses a simple LLM call.
    When multiple routes exist, the LLM decides which to invoke.

    Returns: task (the route name)
    """
    repo_url = state.get("repo_url", "")
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("orchestrator")

    try:
        # Fast path: if only one route exists, skip LLM entirely
        if len(AVAILABLE_ROUTES) == 1:
            task = next(iter(AVAILABLE_ROUTES))
            if tracker:
                tracker.record_tool_call("route_task", f"direct → {task}")
            return {"task": task}

        # Multi-route: use Haiku to decide
        llm = get_llm(temperature=0, max_tokens=50)

        routes_desc = "\n".join(
            f'- "{name}": {desc}' for name, desc in AVAILABLE_ROUTES.items()
        )

        prompt = f"""Given this repository and task, select the appropriate action.

Repository: {repo_url}
Task: {state.get("task", "update dependencies")}

Available actions:
{routes_desc}

Return ONLY the action name as a JSON string: {{"action": "action_name"}}"""

        response = llm.invoke(prompt)

        if tracker:
            usage = getattr(response, "usage_metadata", None)
            if usage:
                tracker.record_llm_call(
                    "claude-haiku-4-5-20251001",
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                )

        try:
            result = json.loads(response.content.strip())
            task = result.get("action", "dependency_update")
        except json.JSONDecodeError:
            task = "dependency_update"

        # Validate route exists
        if task not in AVAILABLE_ROUTES:
            task = "dependency_update"

        return {"task": task}

    except Exception:
        return {"task": "dependency_update"}
    finally:
        if tracker:
            tracker.end_phase()
