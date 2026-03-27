"""
Real-time agent activity logging via LangChain callbacks.

Provides verbose, human-readable console output showing what each agent
is doing: LLM thinking, tool calls, tool results, and errors.
"""

import json
import logging
import time
from typing import Any, Optional
from uuid import UUID

from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

logger = logging.getLogger("app.agent_activity")

# Anthropic pricing per 1M tokens (Claude Sonnet 4.5)
# https://docs.anthropic.com/en/docs/about-claude/pricing
ANTHROPIC_PRICING = {
    "claude-sonnet-4-5-20250929": {
        "input_per_1m": 3.00,
        "output_per_1m": 15.00,
    },
    "claude-sonnet-4-20250514": {
        "input_per_1m": 3.00,
        "output_per_1m": 15.00,
    },
    "claude-haiku-4-5-20251001": {
        "input_per_1m": 0.80,
        "output_per_1m": 4.00,
    },
    "claude-opus-4-6": {
        "input_per_1m": 15.00,
        "output_per_1m": 75.00,
    },
}

# Default fallback pricing (Sonnet 4.5)
DEFAULT_PRICING = {"input_per_1m": 3.00, "output_per_1m": 15.00}


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    ORCHESTRATOR = "\033[1;35m"  # Bold Magenta
    ANALYZER = "\033[1;36m"  # Bold Cyan
    UPDATER = "\033[1;33m"  # Bold Yellow

    THINKING = "\033[34m"  # Blue
    TOOL_CALL = "\033[32m"  # Green
    TOOL_RESULT = "\033[90m"  # Gray
    ERROR = "\033[1;31m"  # Bold Red
    SUCCESS = "\033[1;32m"  # Bold Green


AGENT_STYLES = {
    "orchestrator": {"prefix": "ORCHESTRATOR", "color": Colors.ORCHESTRATOR},
    "analyzer": {"prefix": "ANALYZER    ", "color": Colors.ANALYZER},
    "updater": {"prefix": "UPDATER     ", "color": Colors.UPDATER},
}


def _truncate(text: str, max_len: int = 200) -> str:
    text = str(text).replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _format_tool_args(input_str: str, max_len: int = 150) -> str:
    try:
        args = json.loads(input_str)
        if isinstance(args, dict):
            parts = []
            for k, v in args.items():
                v_str = str(v)
                if len(v_str) > 60:
                    v_str = v_str[:60] + "..."
                parts.append(f"{k}={v_str}")
            return ", ".join(parts)
    except (json.JSONDecodeError, TypeError):
        pass
    return _truncate(input_str, max_len)


def _extract_tool_result_summary(output: Any, max_len: int = 200) -> str:
    output_str = str(output)
    try:
        data = json.loads(output_str)
        if isinstance(data, dict):
            summary_parts = []
            for key in [
                "status",
                "message",
                "repo_path",
                "language",
                "package_manager",
                "outdated_count",
                "total_updates",
                "pr_url",
                "issue_url",
                "branch_name",
                "succeeded",
                "from_cache",
            ]:
                if key in data:
                    val = str(data[key])
                    if len(val) > 80:
                        val = val[:80] + "..."
                    summary_parts.append(f"{key}={val}")
            if summary_parts:
                return ", ".join(summary_parts[:5])
    except (json.JSONDecodeError, TypeError):
        pass
    return _truncate(output_str, max_len)


class AgentActivityHandler(BaseCallbackHandler):
    """
    LangChain callback handler that provides verbose, real-time logging
    of agent activity to console and Python logging framework.

    Usage:
        handler = AgentActivityHandler("orchestrator")
        agent.invoke({"messages": [...]}, config={"callbacks": [handler]})
    """

    def __init__(self, agent_name: str, job_id: Optional[str] = None):
        self.agent_name = agent_name
        self.job_id = job_id
        self._style = AGENT_STYLES.get(
            agent_name, {"prefix": agent_name.upper(), "color": Colors.BOLD}
        )
        self._llm_start_time: Optional[float] = None
        self._tool_start_times: dict[UUID, float] = {}
        self.activity_log: list[dict[str, Any]] = []

        # Token usage tracking
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.llm_call_count: int = 0
        self._model_name: Optional[str] = None

        # Sub-handler tracking (for aggregating child agent costs)
        self._child_handlers: list["AgentActivityHandler"] = []

    def _prefix(self) -> str:
        color = self._style["color"]
        prefix = self._style["prefix"]
        return f"{color}[{prefix}]{Colors.RESET}"

    def _log_activity(self, event_type: str, detail: str, **extra: Any) -> None:
        entry = {
            "agent": self.agent_name,
            "event": event_type,
            "detail": detail,
            "job_id": self.job_id,
            **extra,
        }
        self.activity_log.append(entry)
        logger.debug(json.dumps(entry))

    # ── LLM / Chat Model Events ─────────────────────────────────

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._llm_start_time = time.time()
        msg_count = sum(len(batch) for batch in messages)

        # Capture model name for pricing lookup
        if self._model_name is None:
            model = kwargs.get("invocation_params", {}).get("model")
            if not model:
                model = serialized.get("kwargs", {}).get("model")
            if model:
                self._model_name = model

        print(
            f"{self._prefix()} {Colors.THINKING}Thinking... "
            f"({msg_count} messages in context){Colors.RESET}"
        )
        self._log_activity("llm_start", f"Chat model invoked with {msg_count} messages")

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        elapsed = ""
        if self._llm_start_time:
            elapsed = f" ({time.time() - self._llm_start_time:.1f}s)"
            self._llm_start_time = None

        token_info = ""
        if response.llm_output and "usage" in response.llm_output:
            usage = response.llm_output["usage"]
            inp = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)
            self.total_input_tokens += inp
            self.total_output_tokens += out
            self.llm_call_count += 1
            token_info = f" | tokens: {inp} in, {out} out"

        # Also check per-generation usage_metadata (LangChain standard)
        if not token_info and response.generations:
            for gen_list in response.generations:
                for gen in gen_list:
                    meta = getattr(gen, "message", None)
                    if meta and hasattr(meta, "usage_metadata") and meta.usage_metadata:
                        inp = meta.usage_metadata.get("input_tokens", 0)
                        out = meta.usage_metadata.get("output_tokens", 0)
                        self.total_input_tokens += inp
                        self.total_output_tokens += out
                        self.llm_call_count += 1
                        token_info = f" | tokens: {inp} in, {out} out"

        print(
            f"{self._prefix()} {Colors.DIM}LLM responded{elapsed}{token_info}"
            f"{Colors.RESET}"
        )
        self._log_activity("llm_end", f"LLM responded{elapsed}{token_info}")

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        print(f"{self._prefix()} {Colors.ERROR}LLM ERROR: {error}{Colors.RESET}")
        self._log_activity("llm_error", str(error))

    # ── Tool Events ──────────────────────────────────────────────

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._tool_start_times[run_id] = time.time()
        tool_name = serialized.get("name", "unknown_tool")
        formatted_args = _format_tool_args(input_str)

        print(
            f"{self._prefix()} {Colors.TOOL_CALL}>>> Calling tool: "
            f"{Colors.BOLD}{tool_name}{Colors.RESET}"
        )
        if formatted_args:
            print(
                f"{self._prefix()} {Colors.DIM}    args: {formatted_args}{Colors.RESET}"
            )

        self._log_activity("tool_start", tool_name, args=_truncate(input_str, 500))

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        elapsed = ""
        if run_id in self._tool_start_times:
            elapsed = f" ({time.time() - self._tool_start_times.pop(run_id):.1f}s)"

        summary = _extract_tool_result_summary(output)

        output_str = str(output)
        is_error = '"status": "error"' in output_str or '"status":"error"' in output_str

        if is_error:
            print(
                f"{self._prefix()} {Colors.ERROR}<<< Tool returned error{elapsed}: "
                f"{summary}{Colors.RESET}"
            )
        else:
            print(
                f"{self._prefix()} {Colors.TOOL_RESULT}<<< Tool result{elapsed}: "
                f"{summary}{Colors.RESET}"
            )

        self._log_activity("tool_end", summary, elapsed=elapsed)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._tool_start_times.pop(run_id, None)
        print(
            f"{self._prefix()} {Colors.ERROR}<<< Tool EXCEPTION: {error}{Colors.RESET}"
        )
        self._log_activity("tool_error", str(error))

    # ── Agent Events ─────────────────────────────────────────────

    def on_agent_action(
        self,
        action: AgentAction,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        print(
            f"{self._prefix()} {Colors.BOLD}Agent decided: {action.tool}{Colors.RESET}"
        )
        self._log_activity("agent_action", action.tool)

    def on_agent_finish(
        self,
        finish: AgentFinish,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        summary = _truncate(finish.return_values.get("output", str(finish.log)), 300)
        print(
            f"{self._prefix()} {Colors.SUCCESS}Agent finished: {summary}{Colors.RESET}"
        )
        self._log_activity("agent_finish", summary)

    # ── Usage / Cost Tracking ──────────────────────────────────────

    def add_child_handler(self, child: "AgentActivityHandler") -> None:
        """Register a child agent's handler so its tokens are included in the total."""
        self._child_handlers.append(child)

    def get_usage_summary(self) -> dict:
        """
        Return aggregated token usage and estimated cost for this agent
        and all its child agents (analyzer, updater).

        Cost is calculated using Anthropic's per-model pricing.
        """
        total_in = self.total_input_tokens
        total_out = self.total_output_tokens
        total_calls = self.llm_call_count

        children_summaries = []
        for child in self._child_handlers:
            child_summary = child.get_usage_summary()
            total_in += child_summary["input_tokens"]
            total_out += child_summary["output_tokens"]
            total_calls += child_summary["llm_calls"]
            children_summaries.append(child_summary)

        pricing = ANTHROPIC_PRICING.get(self._model_name, DEFAULT_PRICING)
        input_cost = (total_in / 1_000_000) * pricing["input_per_1m"]
        output_cost = (total_out / 1_000_000) * pricing["output_per_1m"]
        total_cost = input_cost + output_cost

        return {
            "agent": self.agent_name,
            "model": self._model_name or "unknown",
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "llm_calls": total_calls,
            "estimated_cost_usd": round(total_cost, 6),
            "cost_breakdown": {
                "input_cost_usd": round(input_cost, 6),
                "output_cost_usd": round(output_cost, 6),
            },
            "children": children_summaries if children_summaries else None,
        }

    # ── Chain Events (minimal, to avoid noise) ───────────────────

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        print(f"{self._prefix()} {Colors.ERROR}Chain error: {error}{Colors.RESET}")
        self._log_activity("chain_error", str(error))
