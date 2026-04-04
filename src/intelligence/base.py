"""
Analyzer protocol — the contract every intelligence analyzer must satisfy.

Uses typing.Protocol for structural subtyping: any class with matching
should_run() and analyze() methods is a valid Analyzer, no inheritance needed.
"""

from typing import Any, Optional, Protocol, runtime_checkable

from src.callbacks.cost_tracker import CostTracker


@runtime_checkable
class Analyzer(Protocol):
    """
    Contract for LLM-powered analysis tasks.

    Each analyzer:
    - Checks preconditions via should_run() (avoids wasted LLM calls)
    - Produces a dict of results via analyze() (merged into pipeline state)
    - Writes its output under a unique state key to avoid collisions
    """

    @property
    def name(self) -> str:
        """Short identifier used in logs and cost tracking (e.g. 'changelog_risk')."""
        ...

    def should_run(self, state: dict) -> bool:
        """Return True if this analyzer has enough data to produce useful output."""
        ...

    def analyze(self, state: dict, tracker: Optional[CostTracker] = None) -> dict:
        """
        Run the analysis and return a dict to merge into pipeline state.

        Must be idempotent — safe to call multiple times with the same state.
        Must handle LLM failures gracefully (return empty dict on error).
        """
        ...


def invoke_llm(prompt: str, max_tokens: int = 500, tracker: Optional[CostTracker] = None,
               phase_name: str = "") -> str:
    """
    Shared LLM invocation with cost tracking and graceful error handling.

    Returns the LLM response text, or empty string on failure.
    """
    try:
        from src.config.llm import get_llm

        llm = get_llm(temperature=0, max_tokens=max_tokens)
        response = llm.invoke(prompt)

        if tracker:
            usage = getattr(response, "usage_metadata", None)
            if usage:
                tracker.record_llm_call(
                    getattr(llm, "model_name", "unknown"),
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                )

        return response.content.strip()

    except Exception as e:
        print(f"  [intelligence:{phase_name}] LLM call failed: {e}")
        return ""
