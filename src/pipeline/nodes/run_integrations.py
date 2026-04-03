"""
Run Integrations node — deterministic, 0 LLM tokens.

Executes detected integration tools (linters, formatters, dependency managers)
against the updated repository. Only runs tools that are installed and runnable.

This node is generic — it iterates over the integration registry results
and never references specific tools by name.
"""

from src.integrations.registry import run_integration
from src.pipeline.state import PipelineState


def run_integrations_node(state: PipelineState) -> dict:
    """
    Run all detected and runnable integration tools.

    Returns: integration_results
    """
    repo_path = state.get("repo_path")
    detected = state.get("detected_integrations") or []
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("run_integrations")

    try:
        # Filter to non-security-scanner tools
        # (security scanners run in the dedicated security_audit node)
        # Include non-runnable tools that have install_command (auto-install)
        to_run = [
            i for i in detected
            if i.get("category") != "security_scanner"
               and (i.get("runnable") or i.get("install_command"))
        ]

        # Report tools that can't be run or auto-installed
        skipped = [
            i for i in detected
            if not i.get("runnable") and not i.get("install_command")
               and i.get("category") != "security_scanner"
        ]
        for s in skipped:
            print(f"  [run_integrations] {s['name']}: not installed, no install command, skipping")

        if not to_run:
            print(f"  [run_integrations] No runnable integration tools to execute")
            return {"integration_results": []}

        results = []
        for integration in to_run:
            name = integration["name"]
            print(f"  [run_integrations] Running {name}...")

            result = run_integration(repo_path, integration)
            results.append(result)

            status_icon = "PASS" if result["status"] == "pass" else result["status"].upper()
            print(f"  [run_integrations] {name}: {status_icon}")

            if tracker:
                tracker.record_tool_call(f"run_{name}")

        return {"integration_results": results}

    except Exception as e:
        print(f"  [run_integrations] Error: {e}")
        return {"integration_results": []}
    finally:
        if tracker:
            tracker.end_phase()
