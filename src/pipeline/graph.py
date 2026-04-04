"""
LangGraph pipeline definition.

Wires together all nodes (deterministic + LLM) with conditional edges
to form the complete dependency update workflow.
"""

import os
import shutil
import time
from typing import Optional

from langgraph.graph import END, StateGraph

from src.callbacks.cost_tracker import CostTracker
from src.pipeline.edges import (
    route_after_analyze,
    route_after_build,
    route_after_llm_analysis,
    route_after_orchestrator,
    route_after_prepare,
    route_after_rollback,
    route_after_security_audit,
    route_after_security_fixes,
    route_after_test,
)
from src.pipeline.nodes.analyze import analyze_node
from src.pipeline.nodes.build_test import build_node, test_node
from src.pipeline.nodes.create_issue import create_issue_node
from src.pipeline.nodes.create_pr import create_pr_node
from src.pipeline.nodes.detect_commands import detect_commands_node
from src.pipeline.nodes.detect_integrations import detect_integrations_node
from src.pipeline.nodes.orchestrator import orchestrator_node
from src.pipeline.nodes.prepare import prepare_node
from src.pipeline.nodes.rollback import rollback_node
from src.pipeline.nodes.apply_security_fixes import apply_security_fixes_node
from src.pipeline.nodes.llm_analysis import llm_analysis_node
from src.pipeline.nodes.run_integrations import run_integrations_node
from src.pipeline.nodes.security_audit import security_audit_node
from src.pipeline.state import PipelineState


def build_graph() -> StateGraph:
    """
    Build and compile the LangGraph pipeline.

    Graph topology:
        orchestrator → analyze → detect_commands → detect_integrations → prepare → build → test
                                                                                                    ↓
        end ← create_pr ← llm_analysis ← security_audit ← run_integrations ← (test pass)
                                                                (test fail) → rollback → build
                                                                (max retries) → llm_analysis → create_issue → end
                                                 (build fail) → llm_analysis → create_issue → end
    """
    graph = StateGraph(PipelineState)

    # ── Add nodes ────────────────────────────────────────────
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("detect_commands", detect_commands_node)
    graph.add_node("detect_integrations", detect_integrations_node)
    graph.add_node("prepare", prepare_node)
    graph.add_node("build", build_node)
    graph.add_node("test", test_node)
    graph.add_node("rollback", rollback_node)
    graph.add_node("run_integrations", run_integrations_node)
    graph.add_node("security_audit", security_audit_node)
    graph.add_node("apply_security_fixes", apply_security_fixes_node)
    graph.add_node("llm_analysis", llm_analysis_node)
    graph.add_node("create_pr", create_pr_node)
    graph.add_node("create_issue", create_issue_node)

    # ── Entry point ──────────────────────────────────────────
    graph.set_entry_point("orchestrator")

    # ── Edges ────────────────────────────────────────────────

    # Orchestrator routes to the appropriate pipeline
    graph.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {"analyze": "analyze"},  # extend this map as you add routes
    )

    # After analyze: continue or end (up_to_date/error)
    graph.add_conditional_edges(
        "analyze",
        route_after_analyze,
        {"detect_commands": "detect_commands", "end": END},
    )

    # detect_commands → detect_integrations → prepare (linear)
    graph.add_edge("detect_commands", "detect_integrations")
    graph.add_edge("detect_integrations", "prepare")

    # After prepare: build (changes applied), security_audit (up_to_date), or end (error)
    graph.add_conditional_edges(
        "prepare",
        route_after_prepare,
        {"build": "build", "security_audit": "security_audit", "end": END},
    )

    # After build: test or llm_analysis (build failure → diagnosis → create_issue)
    graph.add_conditional_edges(
        "build",
        route_after_build,
        {"test": "test", "llm_analysis": "llm_analysis"},
    )

    # After test: run_integrations (pass), rollback (fail, retries left), llm_analysis (max retries — for failure diagnosis)
    graph.add_conditional_edges(
        "test",
        route_after_test,
        {"run_integrations": "run_integrations", "rollback": "rollback", "llm_analysis": "llm_analysis"},
    )

    # After rollback: retry build or give up
    graph.add_conditional_edges(
        "rollback",
        route_after_rollback,
        {"build": "build", "create_issue": "create_issue"},
    )

    # run_integrations → security_audit
    graph.add_edge("run_integrations", "security_audit")

    # After security_audit: llm_analysis (has updates), apply_security_fixes, or end
    graph.add_conditional_edges(
        "security_audit",
        route_after_security_audit,
        {"llm_analysis": "llm_analysis", "apply_security_fixes": "apply_security_fixes", "end": END},
    )

    # After apply_security_fixes: llm_analysis (has fixes), create_issue (unfixable CVEs), or end
    graph.add_conditional_edges(
        "apply_security_fixes",
        route_after_security_fixes,
        {"llm_analysis": "llm_analysis", "create_issue": "create_issue", "end": END},
    )

    # After llm_analysis: create_pr or create_issue (failure path)
    graph.add_conditional_edges(
        "llm_analysis",
        route_after_llm_analysis,
        {"create_pr": "create_pr", "create_issue": "create_issue"},
    )

    graph.add_edge("create_pr", END)

    # create_issue → END
    graph.add_edge("create_issue", END)

    return graph.compile()


def _validate_repo_ownership(repo_name: str):
    """
    Verify the authenticated user has push access to the target repo.

    Checks that the authenticated token has push access to the target repo.
    Prevents the pipeline from modifying repos the user can't write to.
    Raises RuntimeError if validation fails.
    """
    import os
    import requests

    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if not token:
        return  # Can't validate without a token; let it fail later

    try:
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

        # Check repo permissions directly — works for personal, org, and fork repos
        repo_resp = requests.get(
            f"https://api.github.com/repos/{repo_name}", headers=headers, timeout=10,
        )
        repo_resp.raise_for_status()
        repo_data = repo_resp.json()

        has_push = repo_data.get("permissions", {}).get("push", False)

        if not has_push:
            repo_owner = repo_data.get("owner", {}).get("login", "")
            raise RuntimeError(
                f"No push access to '{repo_name}' (owner: {repo_owner}). "
                f"This pipeline can only update repositories you have write access to."
            )

        print(f"  [validate] Push access confirmed for {repo_name}")

    except requests.RequestException as e:
        print(f"  [validate] Warning: Could not verify repo access: {e}")
        # Don't block on network errors — let the pipeline proceed


def run_pipeline(
    repo_url: str,
    job_id: Optional[str] = None,
    event_loop=None,
) -> dict:
    """
    Run the full dependency update pipeline.

    Args:
        repo_url: Repository URL or owner/repo
        job_id: Optional job ID for tracking
        event_loop: Event loop for MCP async calls (from FastAPI)

    Returns:
        dict with: status, url, message, usage, activity_log
    """
    # Set event loop for MCP calls if provided
    if event_loop:
        from src.integrations.mcp_server_manager import set_main_event_loop
        set_main_event_loop(event_loop)

    # Normalize URL
    if not repo_url.startswith("http"):
        full_url = f"https://github.com/{repo_url}"
        repo_name = repo_url
    else:
        full_url = repo_url
        parts = full_url.rstrip("/").split("/")
        repo_name = f"{parts[-2]}/{parts[-1]}"

    # Validate repo ownership — only allow updating repos owned by the token holder
    _validate_repo_ownership(repo_name)

    # Initialize cost tracker
    tracker = CostTracker(job_id=job_id)

    # Build initial state
    initial_state: PipelineState = {
        "repo_url": full_url,
        "repo_name": repo_name,
        "task": "dependency_update",
        "retry_count": 0,
        "rollback_history": [],
        "has_tests": True,
        "has_test_command": True,
        "outdated_count": 0,
        "cost_tracker": tracker,
    }

    # Compile and run the graph
    app = build_graph()

    start_time = time.time()
    final_state = {}

    try:
        final_state = app.invoke(initial_state)
    finally:
        elapsed = round(time.time() - start_time, 1)

        # Clean up cloned repo to prevent /tmp disk leak
        repo_path = final_state.get("repo_path") or initial_state.get("repo_path")
        if repo_path and os.path.isdir(repo_path):
            try:
                shutil.rmtree(repo_path)
                print(f"  [cleanup] Removed temp repo: {repo_path}")
            except OSError as e:
                print(f"  [cleanup] Warning: could not remove {repo_path}: {e}")

    # Extract results
    status = final_state.get("final_status", "error")
    url = final_state.get("final_url", "")
    message = final_state.get("final_message", "")
    usage = tracker.get_summary()

    # Security audit summary
    audit_results = final_state.get("audit_results") or []
    total_findings = sum(r.get("finding_count", 0) for r in audit_results)

    print(f"\n{'=' * 60}")
    print(f"  PIPELINE RESULT ({elapsed}s)")
    print(f"{'=' * 60}")
    print(f"  Status:  {status}")
    if url:
        print(f"  URL:     {url}")
    if message:
        print(f"  Message: {message}")

    # Print security fix results
    security_fixes = final_state.get("security_fixes_applied") or []
    unfixable = final_state.get("unfixable_cves") or []

    if security_fixes:
        print()
        print(f"  Security Fixes: {len(security_fixes)} CVE(s) patched")
        for sf in security_fixes:
            print(f"    [FIXED] {sf['name']}: {sf.get('old', '?')} → {sf['new']} ({sf.get('vulnerability', '')})")

    if unfixable:
        print(f"  Unfixable: {len(unfixable)} CVE(s) — no fix available or not a direct dependency")
        for uf in unfixable[:5]:
            print(f"    [TODO]  {uf.get('vulnerability', '')}: {uf['package']} — {uf.get('detail', '')[:80]}")
        if len(unfixable) > 5:
            print(f"    ... and {len(unfixable) - 5} more")

    # Print security audit results
    if audit_results:
        print()
        if total_findings == 0:
            print(f"  Security: No vulnerabilities found")
        else:
            print(f"  Security: {total_findings} finding(s) detected")
        for r in audit_results:
            icon = "PASS" if r.get("finding_count", 0) == 0 else "WARN"
            print(f"    [{icon}] {r.get('source', '')}: {r.get('finding_count', 0)} findings")
            for f in r.get("findings", [])[:5]:
                print(f"          - {f.get('vulnerability', '')}: {f.get('package', '')} — {f.get('detail', '')[:80]}")
            if r.get("finding_count", 0) > 5:
                print(f"          ... and {r['finding_count'] - 5} more")

    print()
    print(f"  Tokens:  {usage['total_tokens']:,} ({usage['llm_calls']} LLM calls)")
    print(f"  Cost:    ${usage['estimated_cost_usd']:.4f}")
    if usage.get("phases"):
        for phase in usage["phases"]:
            tokens_str = f"{phase['tokens']:,} tok" if phase["tokens"] else "0 tok"
            print(f"    - {phase['name']}: {phase['duration_seconds']}s, {tokens_str}")
    print(f"{'=' * 60}\n")

    return {
        "status": status,
        "url": url,
        "message": message,
        "repository": repo_name,
        "usage": usage,
        "audit_results": audit_results,
        "activity_log": tracker.activity_log,
        "elapsed_seconds": elapsed,
    }


def run_pipeline_batch(
    repo_urls: list[str],
    job_id: Optional[str] = None,
) -> dict:
    """
    Run the pipeline across multiple repos, then synthesize cross-repo intelligence.

    Args:
        repo_urls: List of repository URLs or owner/repo strings
        job_id: Optional batch job ID

    Returns:
        dict with: results (per-repo), multi_repo_summary, total_usage
    """
    from src.intelligence.multi_repo import synthesize_multi_repo

    batch_tracker = CostTracker(job_id=job_id)
    results = []

    for url in repo_urls:
        print(f"\n{'─' * 60}")
        print(f"  Processing: {url}")
        print(f"{'─' * 60}")
        try:
            result = run_pipeline(url, job_id=job_id)
            results.append(result)
        except Exception as e:
            results.append({
                "repository": url,
                "status": "error",
                "message": str(e),
                "audit_results": [],
            })

    # Cross-repo intelligence synthesis
    summary = synthesize_multi_repo(results, tracker=batch_tracker)
    batch_usage = batch_tracker.get_summary()

    # Aggregate usage across all runs
    total_tokens = batch_usage["total_tokens"] + sum(
        r.get("usage", {}).get("total_tokens", 0) for r in results
    )
    total_cost = batch_usage["estimated_cost_usd"] + sum(
        r.get("usage", {}).get("estimated_cost_usd", 0) for r in results
    )

    print(f"\n{'=' * 60}")
    print(f"  BATCH RESULT ({len(results)} repos)")
    print(f"{'=' * 60}")
    print(f"\n{summary}\n")
    print(f"  Total tokens: {total_tokens:,}")
    print(f"  Total cost:   ${total_cost:.4f}")
    print(f"{'=' * 60}\n")

    return {
        "results": results,
        "multi_repo_summary": summary,
        "total_usage": {
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(total_cost, 6),
            "repos_processed": len(results),
        },
    }
