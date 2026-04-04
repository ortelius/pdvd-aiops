"""
LLM-powered intelligence layer for the dependency update pipeline.

Each analyzer follows a Protocol interface (Dependency Inversion) and handles
a single analysis concern (Single Responsibility). New analyzers can be added
without modifying existing code (Open/Closed).

Analyzers are registered in ANALYZERS and executed by the llm_analysis node.
"""

from src.intelligence.base import Analyzer
from src.intelligence.changelog import ChangelogRiskAnalyzer
from src.intelligence.failure_diagnosis import FailureDiagnosisAnalyzer
from src.intelligence.pr_summary import MaintainerSummaryAnalyzer
from src.intelligence.security_prioritizer import SecurityPrioritizationAnalyzer

# Registry of all analyzers — order determines execution order.
# Each analyzer's should_run() decides whether it actually fires.
ANALYZERS: list[Analyzer] = [
    ChangelogRiskAnalyzer(),
    SecurityPrioritizationAnalyzer(),
    FailureDiagnosisAnalyzer(),
    MaintainerSummaryAnalyzer(),
]

__all__ = [
    "Analyzer",
    "ANALYZERS",
    "ChangelogRiskAnalyzer",
    "SecurityPrioritizationAnalyzer",
    "FailureDiagnosisAnalyzer",
    "MaintainerSummaryAnalyzer",
]
