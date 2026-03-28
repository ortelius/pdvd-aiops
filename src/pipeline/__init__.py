"""
LangGraph-based dependency update pipeline.

Replaces the three-agent chain (Orchestrator -> Analyzer -> Updater) with a
graph of deterministic nodes and focused LLM nodes, reducing token usage by ~78%.
"""


def build_graph():
    from src.pipeline.graph import build_graph as _build
    return _build()


def run_pipeline(*args, **kwargs):
    from src.pipeline.graph import run_pipeline as _run
    return _run(*args, **kwargs)


__all__ = ["build_graph", "run_pipeline"]
