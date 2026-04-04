"""
LLM factory — single place to configure which model/provider the pipeline uses.

Supports: Anthropic, Google Gemini, OpenAI, Groq, Hugging Face, Ollama.
Configured via environment variables:
    LLM_PROVIDER=huggingface     (default: anthropic)
    LLM_MODEL_NAME=meta-llama/Llama-3.3-70B-Instruct  (default: per-provider)
"""

import os
from typing import Optional

from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage

load_dotenv()


class _LLMLogger(BaseCallbackHandler):
    """Logs prompt and response for every LLM call. Enabled via LLM_DEBUG=true."""

    def on_chat_model_start(self, serialized, messages: list[list[BaseMessage]], **kwargs):
        # messages is a list of batches; we typically have one batch
        for batch in messages:
            for msg in batch:
                role = msg.type  # "human", "system", "ai"
                content = msg.content
                if len(content) > 500:
                    content = content[:500] + f"... ({len(msg.content)} chars)"
                print(f"  [llm:prompt] [{role}] {content}")

    def on_llm_end(self, response, **kwargs):
        for gen_list in response.generations:
            for gen in gen_list:
                content = gen.text
                if len(content) > 500:
                    content = content[:500] + f"... ({len(gen.text)} chars)"
                print(f"  [llm:response] {content}")


_llm_debug = os.getenv("LLM_DEBUG", "").lower() in ("true", "1", "yes")

# API key env var per provider (None = no key required)
PROVIDER_API_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "huggingface": "HF_TOKEN",
    "ollama": None,
}

# Default models per provider
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5-20250929",
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "huggingface": "meta-llama/Llama-3.3-70B-Instruct:groq",
    "ollama": "llama3",
}


def get_required_api_key() -> tuple[Optional[str], str]:
    """
    Return (env_var_name, env_var_value) for the active LLM provider.

    Returns (None, "") for providers that don't need an API key (e.g. Ollama).
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    env_var = PROVIDER_API_KEYS.get(provider)
    if env_var is None:
        return None, ""
    return env_var, os.getenv(env_var, "")


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

    import inspect
    caller = inspect.stack()[1]
    caller_name = f"{os.path.basename(caller.filename)}:{caller.function}"
    tokens_str = f", max_tokens={max_tokens}" if max_tokens else ""
    print(f"  [llm] {caller_name} → {provider}/{model}{tokens_str}")

    kwargs = {"temperature": temperature}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if _llm_debug:
        kwargs["callbacks"] = [_LLMLogger()]

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

    elif provider == "huggingface" or provider == "hf":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            base_url="https://router.huggingface.co/v1",
            api_key=os.getenv("HF_TOKEN", ""),
            **kwargs,
        )

    elif provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, **kwargs)

    else:
        raise ValueError(
            f"Unknown LLM provider: {provider}. "
            f"Supported: anthropic, gemini, openai, groq, huggingface, ollama"
        )
