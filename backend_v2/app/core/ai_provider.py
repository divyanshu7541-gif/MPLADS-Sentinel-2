"""
Centralised access to Google Gemma 4 through OpenAI-compatible inference APIs.

DigitalOcean Serverless Inference is preferred when configured. If its key
is absent, OpenRouter can serve the free Gemma 4 model for development and
hackathon demonstrations. Credentials remain server-side.

Gemma only explains stored evidence. It never reads or writes risk scores.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from .chat_config import get_setting, mask_key

log = logging.getLogger("sentinel.ai")

DEFAULT_BASE_URL = "https://inference.do-ai.run/v1"
DEFAULT_MODEL = "gemma-4-31B-it"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = "google/gemma-4-31b-it:free"
DEFAULT_TIMEOUT_SECONDS = 45.0


class ProviderNotConfigured(RuntimeError):
    """No API key is configured; callers should use their non-AI fallback."""


class ProviderTimeout(RuntimeError):
    """The inference provider did not answer within the configured timeout."""


class ProviderError(RuntimeError):
    """The provider answered with an error or an unusable response."""


def get_provider() -> str | None:
    """Prefer DigitalOcean; use OpenRouter only when the DO key is absent."""
    if get_setting("DO_INFERENCE_API_KEY", "").strip():
        return "digitalocean"
    if get_setting("OPENROUTER_API_KEY", "").strip():
        return "openrouter"
    return None


def get_api_key() -> str:
    provider = get_provider()
    if provider == "digitalocean":
        return get_setting("DO_INFERENCE_API_KEY", "").strip()
    if provider == "openrouter":
        return get_setting("OPENROUTER_API_KEY", "").strip()
    return ""


def get_model() -> str:
    if get_provider() == "openrouter":
        value = get_setting("OPENROUTER_GEMMA_MODEL", OPENROUTER_MODEL)
        return value.strip() or OPENROUTER_MODEL
    value = get_setting("DO_GEMMA_MODEL", DEFAULT_MODEL)
    return value.strip() or DEFAULT_MODEL


def get_base_url() -> str:
    if get_provider() == "openrouter":
        value = get_setting("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL)
        return (value.strip() or OPENROUTER_BASE_URL).rstrip("/")
    value = get_setting("DO_INFERENCE_BASE_URL", DEFAULT_BASE_URL)
    return (value.strip() or DEFAULT_BASE_URL).rstrip("/")


def get_attribution() -> str:
    provider = get_provider()
    if provider == "digitalocean":
        return "Powered by Google Gemma 4 · DigitalOcean Serverless Inference"
    if provider == "openrouter":
        return "Powered by Google Gemma 4 · OpenRouter (free model)"
    return "Google Gemma 4 · Inference provider not configured"


def is_configured() -> bool:
    return bool(get_api_key())


def get_timeout_seconds() -> float:
    raw = get_setting("DO_INFERENCE_TIMEOUT", str(int(DEFAULT_TIMEOUT_SECONDS)))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return min(120.0, max(5.0, value))


def _safe_message(exc: Exception, api_key: str) -> str:
    """An exception summary that can never contain the key itself."""
    message = f"{type(exc).__name__}: {exc}"
    if api_key and api_key in message:
        message = message.replace(api_key, mask_key(api_key))
    return message[:500]


@lru_cache(maxsize=8)
def _get_client(api_key: str, base_url: str, timeout: float):
    from openai import OpenAI

    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
    )


def generate_text(
    system_prompt: str,
    user_prompt: str,
    *,
    history: list[dict] | None = None,
    temperature: float = 0.2,
    max_completion_tokens: int = 1200,
) -> str:
    """Generate plain text from the configured Gemma 4 inference provider.

    History is an optional list of user/assistant turns inserted before the
    current prompt. Provider exceptions never include a raw API key.
    """
    api_key = get_api_key()
    if not api_key:
        raise ProviderNotConfigured("No Gemma inference API key is configured")
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            NotFoundError,
            PermissionDeniedError,
            RateLimitError,
        )
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        log.warning("AI call skipped: the 'openai' package is not installed.")
        raise ProviderError("AI provider library is not installed") from exc

    try:
        client = _get_client(api_key, get_base_url(), get_timeout_seconds())
    except Exception as exc:
        log.warning(
            "AI client could not be created (key %s): %s",
            mask_key(api_key),
            _safe_message(exc, api_key),
        )
        raise ProviderError("AI provider client could not be created") from exc

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for turn in history or []:
        role = turn.get("role")
        content = str(turn.get("content", "")).strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_prompt})

    try:
        response = client.chat.completions.create(
            model=get_model(),
            messages=messages,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
        )
    except APITimeoutError as exc:
        log.warning("AI request timed out (provider %s, model %s)", get_provider(), get_model())
        raise ProviderTimeout("AI request timed out") from exc
    except AuthenticationError as exc:
        log.warning(
            "AI authentication failed (provider %s, key %s): %s",
            get_provider(),
            mask_key(api_key),
            _safe_message(exc, api_key),
        )
        raise ProviderError("AI provider rejected the credentials") from exc
    except PermissionDeniedError as exc:
        log.warning(
            "AI permission denied (provider %s, key %s): %s",
            get_provider(),
            mask_key(api_key),
            _safe_message(exc, api_key),
        )
        raise ProviderError("AI provider denied access to the model") from exc
    except NotFoundError as exc:
        log.warning(
            "AI model not found (provider %s, model %s): %s",
            get_provider(),
            get_model(),
            _safe_message(exc, api_key),
        )
        raise ProviderError("AI model is not available") from exc
    except RateLimitError as exc:
        log.warning(
            "AI rate limit reached (provider %s, key %s): %s",
            get_provider(),
            mask_key(api_key),
            _safe_message(exc, api_key),
        )
        raise ProviderError("AI provider rate limit reached") from exc
    except BadRequestError as exc:
        log.warning(
            "AI request rejected (provider %s, model %s): %s",
            get_provider(),
            get_model(),
            _safe_message(exc, api_key),
        )
        raise ProviderError("AI provider rejected the request") from exc
    except APIConnectionError as exc:
        log.warning(
            "AI connection failed (provider %s, key %s): %s",
            get_provider(),
            mask_key(api_key),
            _safe_message(exc, api_key),
        )
        raise ProviderError("AI provider could not be reached") from exc
    except APIStatusError as exc:
        status = getattr(exc, "status_code", "?")
        log.warning(
            "AI provider error %s (provider %s, key %s): %s",
            status,
            get_provider(),
            mask_key(api_key),
            _safe_message(exc, api_key),
        )
        raise ProviderError(f"AI provider error {status}") from exc
    except Exception as exc:
        log.warning(
            "AI call failed (provider %s, key %s): %s",
            get_provider(),
            mask_key(api_key),
            _safe_message(exc, api_key),
        )
        raise ProviderError("AI provider call failed") from exc

    try:
        content = (response.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, TypeError) as exc:
        raise ProviderError("AI provider returned no choices") from exc
    if not content:
        raise ProviderError("AI provider returned an empty response")
    return content
