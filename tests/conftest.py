"""
Test configuration.

Loads dummy environment variables from tests/.env.test to prevent
accidental API calls during testing. Override values in .env.test
if you need to run integration tests against real services.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load test env BEFORE any other imports that might use these vars
_test_env = Path(__file__).parent / ".env.test"
if _test_env.exists():
    load_dotenv(_test_env, override=True)
