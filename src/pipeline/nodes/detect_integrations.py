"""
Detect Integrations node — deterministic, 0 LLM tokens.

Scans the cloned repository for configured DevOps/DevSecOps integration tools.
Uses the integration registry to discover tools by config file presence.
"""

from src.integrations.registry import get_runnable_integrations
from src.pipeline.state import PipelineState


def detect_integrations_node(state: PipelineState) -> dict:
    """
    Detect configured integration tools in the repository.

    Returns: detected_integrations
    """
    repo_path = state.get("repo_path")
    language = state.get("language")
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("detect_integrations")

    try:
        if not repo_path:
            return {"detected_integrations": []}

        integrations = get_runnable_integrations(repo_path, ecosystem=language)

        runnable = [i for i in integrations if i.get("runnable")]
        detected_only = [i for i in integrations if not i.get("runnable")]

        if runnable:
            names = [i["name"] for i in runnable]
            print(f"  [detect_integrations] Found {len(runnable)} runnable: {', '.join(names)}")
        if detected_only:
            names = [i["name"] for i in detected_only]
            print(f"  [detect_integrations] Configured but not installed: {', '.join(names)}")
        if not integrations:
            print(f"  [detect_integrations] No integration tools detected")

        if tracker:
            tracker.record_tool_call("detect_integrations")

        return {"detected_integrations": integrations}

    except Exception as e:
        print(f"  [detect_integrations] Error: {e}")
        return {"detected_integrations": []}
    finally:
        if tracker:
            tracker.end_phase()
