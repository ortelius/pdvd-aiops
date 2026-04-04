"""
LLM Analysis node — orchestrates all intelligence analyzers.

Runs after security_audit (all data is available) and before create_pr/create_issue.
Each registered analyzer checks its own preconditions and only fires when relevant.

Cost: 0–4 cheap LLM calls depending on pipeline state (~$0.005 total worst case).
"""

from src.intelligence import ANALYZERS
from src.pipeline.state import PipelineState


def llm_analysis_node(state: PipelineState) -> dict:
    """
    Run all applicable intelligence analyzers and merge results into state.

    Each analyzer:
    1. Checks should_run() — skips if preconditions aren't met
    2. Runs analyze() — produces a dict of results
    3. Results are merged into the returned state update

    Returns: dict with analysis fields (e.g. changelog_risk_summary,
             security_priority_summary, failure_diagnosis, maintainer_summary)
    """
    tracker = state.get("cost_tracker")

    if tracker:
        tracker.start_phase("llm_analysis")

    results = {}

    try:
        for analyzer in ANALYZERS:
            if not analyzer.should_run(state):
                print(f"  [llm_analysis] Skipping {analyzer.name} (preconditions not met)")
                continue

            print(f"  [llm_analysis] Running {analyzer.name}...")
            analysis = analyzer.analyze(state, tracker=tracker)

            if analysis:
                results.update(analysis)
                print(f"  [llm_analysis] {analyzer.name} produced {len(analysis)} field(s)")
            else:
                print(f"  [llm_analysis] {analyzer.name} returned no results")

        # Log summary
        active = [a.name for a in ANALYZERS if a.should_run(state)]
        produced = list(results.keys())
        print(f"  [llm_analysis] Ran {len(active)} analyzer(s), produced fields: {produced}")

    except Exception as e:
        print(f"  [llm_analysis] Error: {e}")
    finally:
        if tracker:
            tracker.end_phase()

    return results
