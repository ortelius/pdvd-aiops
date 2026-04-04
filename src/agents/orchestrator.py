#!/usr/bin/env python3
"""
Backwards-compatibility shim — real implementation is in src.cli.main.

Keeps `python -m src.agents.orchestrator <repo>` working.
"""

from src.cli.main import main, validate_prerequisites

__all__ = ["main", "validate_prerequisites"]

if __name__ == "__main__":
    main()
