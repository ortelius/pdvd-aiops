"""
Unified cost tracking across deterministic and LLM phases.

Tracks token usage, LLM calls, tool calls, and estimated cost
across the entire pipeline — both deterministic nodes (0 tokens)
and LLM agent nodes.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from src.callbacks.agent_activity import ANTHROPIC_PRICING, DEFAULT_PRICING


@dataclass
class PhaseMetrics:
    """Metrics for a single pipeline phase."""

    phase_name: str
    start_time: float = 0.0
    end_time: Optional[float] = None
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    model_name: Optional[str] = None
    estimated_cost_usd: float = 0.0

    @property
    def duration_seconds(self) -> float:
        if self.end_time:
            return round(self.end_time - self.start_time, 1)
        return round(time.time() - self.start_time, 1)

    def compute_cost(self):
        pricing = ANTHROPIC_PRICING.get(self.model_name, DEFAULT_PRICING)
        input_cost = (self.input_tokens / 1_000_000) * pricing["input_per_1m"]
        output_cost = (self.output_tokens / 1_000_000) * pricing["output_per_1m"]
        self.estimated_cost_usd = round(input_cost + output_cost, 6)


class CostTracker:
    """
    Unified cost tracker for the pipeline.

    Usage:
        tracker = CostTracker()
        tracker.start_phase("analyze")
        tracker.record_tool_call()  # deterministic tool
        tracker.end_phase()

        tracker.start_phase("verification_agent")
        tracker.record_llm_call(model, input_tokens, output_tokens)
        tracker.end_phase()

        summary = tracker.get_summary()
    """

    def __init__(self, job_id: Optional[str] = None):
        self.job_id = job_id
        self.phases: list[PhaseMetrics] = []
        self._current_phase: Optional[PhaseMetrics] = None
        self.activity_log: list[dict[str, Any]] = []

    def start_phase(self, name: str) -> None:
        phase = PhaseMetrics(phase_name=name, start_time=time.time())
        self.phases.append(phase)
        self._current_phase = phase
        self._log("phase_start", name)

    def end_phase(self) -> None:
        if self._current_phase:
            self._current_phase.end_time = time.time()
            self._current_phase.compute_cost()
            self._log("phase_end", self._current_phase.phase_name,
                       duration=self._current_phase.duration_seconds)
            self._current_phase = None

    def record_tool_call(self, tool_name: str = "", detail: str = "") -> None:
        if self._current_phase:
            self._current_phase.tool_calls += 1
        self._log("tool_call", f"{tool_name}: {detail}" if detail else tool_name)

    def record_llm_call(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> None:
        if self._current_phase:
            self._current_phase.llm_calls += 1
            self._current_phase.input_tokens += input_tokens
            self._current_phase.output_tokens += output_tokens
            self._current_phase.model_name = model
        self._log("llm_call", model,
                   input_tokens=input_tokens, output_tokens=output_tokens)

    def merge_agent_handler(self, handler: Any) -> None:
        """
        Merge token counts from a LangChain AgentActivityHandler
        into the current phase.
        """
        if self._current_phase and handler:
            self._current_phase.input_tokens += handler.total_input_tokens
            self._current_phase.output_tokens += handler.total_output_tokens
            self._current_phase.llm_calls += handler.llm_call_count
            if handler._model_name:
                self._current_phase.model_name = handler._model_name

    def _log(self, event: str, detail: str, **extra: Any) -> None:
        self.activity_log.append({
            "event": event,
            "detail": detail,
            "job_id": self.job_id,
            "time": time.time(),
            **extra,
        })

    def get_summary(self) -> dict:
        """
        Return full cost summary across all phases.

        Output format is compatible with the existing UsageResponse model
        in server.py, with additional per-phase breakdown.
        """
        total_in = sum(p.input_tokens for p in self.phases)
        total_out = sum(p.output_tokens for p in self.phases)
        total_cost = sum(p.estimated_cost_usd for p in self.phases)
        total_llm = sum(p.llm_calls for p in self.phases)
        total_tools = sum(p.tool_calls for p in self.phases)

        return {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "llm_calls": total_llm,
            "tool_calls": total_tools,
            "estimated_cost_usd": round(total_cost, 6),
            "phases": [
                {
                    "name": p.phase_name,
                    "duration_seconds": p.duration_seconds,
                    "input_tokens": p.input_tokens,
                    "output_tokens": p.output_tokens,
                    "tokens": p.input_tokens + p.output_tokens,
                    "llm_calls": p.llm_calls,
                    "tool_calls": p.tool_calls,
                    "model": p.model_name,
                    "cost_usd": p.estimated_cost_usd,
                }
                for p in self.phases
            ],
        }
