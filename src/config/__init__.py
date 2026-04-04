import os
from src.config.llm import DEFAULT_MODELS

_provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
DEFAULT_LLM_MODEL = os.getenv("LLM_MODEL_NAME", DEFAULT_MODELS.get(_provider, "claude-sonnet-4-5-20250929"))
