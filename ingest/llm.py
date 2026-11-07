"""Shared OpenAI-compatible LLM client.

One client for every optional LLM feature (quality validation, LLM redaction).
It speaks the OpenAI Chat Completions API, which is the de-facto standard that
local/self-hosted servers also expose — vLLM, LM Studio, llama.cpp's server,
Ollama, LiteLLM, etc. For privacy, run a LOCAL endpoint so your chat text never
leaves your machine; that is the intended setup for this project.

Environment variables:
  LLM_VALIDATE   true/false. Default: enabled when LLM_API_KEY or
                 LLM_API_BASE_URL is set, disabled otherwise.
  LLM_API_BASE_URL  Base URL of your local OpenAI-compatible server, e.g.
                 http://localhost:8000/v1 (vLLM) or http://localhost:1234/v1
                 (LM Studio).
  LLM_MODEL      Model id your server serves — required to use the LLM features
                 (no default). Use the HF repo id, as vLLM / LM Studio do
                 (e.g. "Qwen/Qwen2.5-7B-Instruct").
  LLM_API_KEY    API key. Local servers usually accept any value.
"""

import os
from typing import Optional

VALIDATE_ENV = "LLM_VALIDATE"
MODEL_ENV = "LLM_MODEL"
BASE_URL_ENV = "LLM_API_BASE_URL"
API_KEY_ENV = "LLM_API_KEY"


def base_url() -> str:
    return os.environ.get(BASE_URL_ENV, "").strip()


def model() -> str:
    """The configured model id, or empty string if unset (no default)."""
    return os.environ.get(MODEL_ENV, "").strip()


def is_local() -> bool:
    """True when a custom (presumably local/self-hosted) endpoint is configured."""
    return bool(base_url())


def _api_key() -> str:
    return (
        os.environ.get(API_KEY_ENV, "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )


def should_validate() -> bool:
    val = os.environ.get(VALIDATE_ENV, "").strip().lower()
    if val == "false":
        return False
    if val == "true":
        return True
    # Default: enable when there's something to talk to.
    return bool(_api_key() or base_url())


def get_client():
    """Build an OpenAI-compatible client. Raises if unusable (caller handles)."""
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError(
            "The 'openai' package is required for LLM features. "
            "Install it with: pip install openai"
        )
    if not model():
        raise EnvironmentError(
            f"{MODEL_ENV} is not set. Set it to the model your local server serves "
            f"(e.g. Qwen/Qwen2.5-7B-Instruct), or set {VALIDATE_ENV}=false."
        )
    url = base_url()
    key = _api_key()
    if not key:
        if url:
            key = "not-needed"  # local servers ignore it, but the SDK requires a value
        else:
            raise EnvironmentError(
                f"{API_KEY_ENV} is not set. Set it, point {BASE_URL_ENV} at a "
                f"local endpoint, or set {VALIDATE_ENV}=false."
            )
    kwargs = {"api_key": key}
    if url:
        kwargs["base_url"] = url
    return OpenAI(**kwargs)


def endpoint_label() -> str:
    return base_url() or "OpenAI API"


def chat(client, model_name: str, prompt: str, max_tokens: int = 256,
         temperature: Optional[float] = None) -> str:
    """Single-prompt completion; returns the assistant message text."""
    kwargs = {"temperature": temperature} if temperature is not None else {}
    resp = client.chat.completions.create(
        model=model_name,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    return (resp.choices[0].message.content or "").strip()
