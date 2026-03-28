"""
Pipeline state definition.

A single TypedDict that flows through every node in the LangGraph.
Each node reads what it needs and writes its outputs — no module-level globals.
"""

from typing import Any, Optional
from typing_extensions import TypedDict


class PipelineState(TypedDict, total=False):
    # ── Input ────────────────────────────────────────────
    task: str  # e.g. "dependency_update"
    repo_url: str
    repo_name: str  # "owner/repo"

    # ── Analyze phase ────────────────────────────────────
    repo_path: Optional[str]
    language: Optional[str]
    package_manager: Optional[str]
    detected_info: Optional[dict]  # full detect_package_manager result
    outdated_packages: Optional[list[dict]]
    outdated_count: int

    # ── Detect commands phase ────────────────────────────
    build_commands: Optional[dict]  # {install, build, test, lint, ...}
    commands_source: Optional[str]  # "ci_config" | "haiku_llm" | "ecosystem_default"

    # ── Prepare phase ────────────────────────────────────
    dependency_file_name: Optional[str]
    original_file_content: Optional[str]
    updated_file_content: Optional[str]
    applied_updates: Optional[list[dict]]

    # ── Build / Test phase ───────────────────────────────
    build_result: Optional[dict]  # {succeeded, exit_code, stdout, stderr}
    test_result: Optional[dict]
    build_log: Optional[str]
    test_log: Optional[str]
    has_tests: bool
    has_test_command: bool

    # ── Rollback loop ────────────────────────────────────
    retry_count: int
    rollback_history: list[dict]  # [{package, from_version, to_version}, ...]

    # ── Verification ─────────────────────────────────────
    verification_results: Optional[list[dict]]  # [{check, status, detail}, ...]

    # ── GitHub operations ────────────────────────────────
    branch_name: Optional[str]
    pr_url: Optional[str]
    issue_url: Optional[str]

    # ── Final result ─────────────────────────────────────
    final_status: Optional[str]  # pr_created | issue_created | up_to_date | error
    final_url: Optional[str]
    final_message: Optional[str]

    # ── Cost tracking ────────────────────────────────────
    cost_tracker: Optional[Any]  # CostTracker instance
