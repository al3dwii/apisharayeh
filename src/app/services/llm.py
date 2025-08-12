from typing import Any, Optional
from app.core.config import settings
from langchain_openai import ChatOpenAI
import os

# Aliases -> provider models
MODEL_ALIASES = {
    "default": "gpt-4o-mini",
    "openai:gpt-4o-mini": "gpt-4o-mini",
    # Local models via OpenAI-compatible API (vLLM/Ollama/LM Studio)
    "local:llama3.1": "llama3.1",
    "local:mixtral": "mixtral",
}

def get_chat(
    model_alias: Optional[str] = None,
    temperature: float = 0.2,
    required: bool = True,
) -> Optional[Any]:
    """
    Return a ChatOpenAI instance or None.
    - If `required` is True and no API base/key is available, raise a RuntimeError.
    - If `required` is False and no API base/key is available, return None (tools-only flow).
    """
    alias = model_alias or "default"
    model_name = MODEL_ALIASES.get(alias, alias)

    # Local OpenAI-compatible servers
    if alias.startswith("local:"):
        base = os.getenv("LOCAL_OPENAI_BASE_URL")
        if not base:
            if required:
                raise RuntimeError("LOCAL_OPENAI_BASE_URL is not set")
            return None
        key = os.getenv("LOCAL_OPENAI_API_KEY") or "local-key"
        return ChatOpenAI(model=model_name, temperature=temperature, api_key=key, base_url=base)

    # OpenAI (official)
    key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
    if not key:
        if required:
            raise RuntimeError("OPENAI_API_KEY is not set")
        return None

    return ChatOpenAI(model=model_name, temperature=temperature, api_key=key)
