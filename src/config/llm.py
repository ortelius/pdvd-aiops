"""
LLM factory — single place to configure which model/provider the pipeline uses.

Supports: Anthropic, Google Gemini, OpenAI, Groq, Ollama.
Configured via environment variables:
    LLM_PROVIDER=gemini          (default: anthropic)
    LLM_MODEL_NAME=gemini-2.0-flash  (default: per-provider)
"""

import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# Default models per provider
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5-20250929",
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "ollama": "llama3",
}


def get_llm(
    temperature: float = 0,
    max_tokens: Optional[int] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
):
    """
    Create a LangChain chat model based on environment config.

    Args:
        temperature: Model temperature (default 0 for deterministic)
        max_tokens: Max output tokens (optional)
        provider: Override LLM_PROVIDER env var
        model: Override LLM_MODEL_NAME env var

    Returns:
        A LangChain BaseChatModel instance
    """
    provider = provider or os.getenv("LLM_PROVIDER", "anthropic").lower()
    model = model or os.getenv("LLM_MODEL_NAME", DEFAULT_MODELS.get(provider, ""))

    kwargs = {"temperature": temperature}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, **kwargs)

    elif provider == "gemini" or provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, **kwargs)

    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, **kwargs)

    elif provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=model, **kwargs)

    elif provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, **kwargs)

    else:
        raise ValueError(
            f"Unknown LLM provider: {provider}. "
            f"Supported: anthropic, gemini, openai, groq, ollama"
        )
