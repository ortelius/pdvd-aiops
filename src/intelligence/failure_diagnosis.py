"""
Test failure diagnosis — Feature #3.

When build/test fails after updates, this analyzer reads the full error output
+ the diff of changes and pinpoints exactly which package update caused the
failure and why — e.g. "Package X v3.0 renamed Config.Timeout to
Config.RequestTimeout, update line 47 of server.go."

Cost: ~1 LLM call, only on failure (after max retries exhausted).
"""

from typing import Optional

from src.callbacks.cost_tracker import CostTracker
from src.intelligence.base import invoke_llm


class FailureDiagnosisAnalyzer:
    """Diagnose build/test failures caused by dependency updates."""

    @property
    def name(self) -> str:
        return "failure_diagnosis"

    def should_run(self, state: dict) -> bool:
        """Run only when build or test has failed."""
        build_result = state.get("build_result") or {}
        test_result = state.get("test_result") or {}
        build_failed = build_result and not build_result.get("succeeded", True)
        test_failed = test_result and not test_result.get("succeeded", True)
        return build_failed or test_failed

    def analyze(self, state: dict, tracker: Optional[CostTracker] = None) -> dict:
        """
        Diagnose the root cause of a build/test failure.

        Returns: {"failure_diagnosis": str}
        """
        build_result = state.get("build_result") or {}
        test_result = state.get("test_result") or {}
        build_log = state.get("build_log", "")
        test_log = state.get("test_log", "")
        applied_updates = state.get("applied_updates") or []
        rollback_history = state.get("rollback_history") or []
        package_manager = state.get("package_manager", "")

        # Determine which phase failed
        if test_result and not test_result.get("succeeded", True):
            failure_type = "test"
            error_output = (test_result.get("stderr", "") + "\n" + test_result.get("stdout", ""))
            log_output = test_log
        else:
            failure_type = "build"
            error_output = (build_result.get("stderr", "") + "\n" + build_result.get("stdout", ""))
            log_output = build_log

        # Build the diff of what changed
        updates_text = _format_updates(applied_updates)
        rollback_text = _format_rollbacks(rollback_history)

        prompt = f"""You are a build engineer diagnosing a dependency update failure.
The automated pipeline updated dependencies, but the {failure_type} failed.

Package manager: {package_manager}

Dependencies updated:
{updates_text}

{f"Rollback attempts (already tried):{chr(10)}{rollback_text}" if rollback_text else "No rollback attempts yet."}

Error output (last 2000 chars):
```
{error_output[-2000:]}
```

{failure_type.capitalize()} log (last 1500 chars):
```
{log_output[-1500:]}
```

Diagnose the failure:
1. Which specific package update most likely caused this failure?
2. What exactly broke? (renamed API, removed function, changed behavior, type mismatch, etc.)
3. What is the specific fix? (e.g. "rename Config.Timeout to Config.RequestTimeout on line X of file Y")
4. If the fix requires code changes beyond version pinning, describe them precisely.

Be specific — reference actual error messages, package names, and version numbers from the output.
Format as concise markdown (no top-level heading). Keep under 350 words."""

        diagnosis = invoke_llm(prompt, max_tokens=500, tracker=tracker, phase_name=self.name)
        if not diagnosis:
            return {}

        return {"failure_diagnosis": diagnosis}


def _format_updates(updates: list[dict]) -> str:
    """Format applied updates for the prompt."""
    if not updates:
        return "No updates applied."
    lines = []
    for u in updates:
        old = u.get("old", "?")
        new = u.get("new", "?")
        bump = _version_bump_type(old, new)
        lines.append(f"- {u['name']}: {old} → {new} ({bump})")
    return "\n".join(lines)


def _format_rollbacks(history: list[dict]) -> str:
    """Format rollback history for the prompt."""
    if not history:
        return ""
    return "\n".join(
        f"- Rolled back {r.get('package', '?')}: {r.get('from_version', '?')} → {r.get('to_version', '?')}"
        for r in history
    )


def _version_bump_type(old: str, new: str) -> str:
    """Classify version bump type."""
    old_parts = old.lstrip("^~>=v").split(".")
    new_parts = new.lstrip("^~>=v").split(".")
    try:
        if old_parts[0] != new_parts[0]:
            return "MAJOR"
        if len(old_parts) >= 2 and len(new_parts) >= 2 and old_parts[1] != new_parts[1]:
            return "minor"
        return "patch"
    except (IndexError, ValueError):
        return "unknown"
