"""
Centralised access to DigitalOcean Serverless Inference (Google Gemma 4).

Every AI feature goes through this module so that credentials, timeouts,
error handling and secret-safe logging are defined in exactly one place.

Configuration (all optional; without a key the AI features degrade to a
deterministic, non-AI path):
  DO_INFERENCE_API_KEY   DigitalOcean model access key (never logged in full,
                         never sent to the frontend, never committed)
  DO_GEMMA_MODEL         model id, default "gemma-4-31B-it"
  DO_INFERENCE_BASE_URL  OpenAI-compatible base URL. DigitalOcean's is
                         "https://inference.do-ai.run/v1" (include /v1: the
                         OpenAI SDK appends "/chat/completions").
  DO_INFERENCE_TIMEOUT   request timeout in seconds, default 30

The provider is deliberately generic: `generate_text` returns plain text.
This application does not rely on structured-output support; callers that
need a fixed shape validate the returned text themselves.

No function here reads or writes a risk score. Gemma is a presentation and
explanation layer over stored evidence, never part of the scoring engine.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from .chat_config import get_setting, mask_key

log = logging.getLogger("sentinel.ai")

DEFAULT_BASE_URL = "https://inference.do-ai.run/v1"
DEFAULT_MODEL = "gemma-4-31B-it"
DEFAULT_TIMEOUT_SECONDS = 30.0


class ProviderNotConfigured(RuntimeError):
    """No API key is configured; callers should use their non-AI fallback."""


class ProviderTimeout(RuntimeError):
    """DigitalOcean did not answer within the configured timeout."""


class ProviderError(RuntimeError):
    """The provider answered with an error or an unusable response."""


def get_api_key() -> str:
    return get_setting("DO_INFERENCE_API_KEY", "")


def get_model() -> str:
    return get_setting("DO_GEMMA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def get_base_url() -> str:
    value = get_setting("DO_INFERENCE_BASE_URL", DEFAULT_BASE_URL).strip()
    return (value or DEFAULT_BASE_URL).rstrip("/")


def get_timeout_seconds() -> float:
    raw = get_setting("DO_INFERENCE_TIMEOUT", str(int(DEFAULT_TIMEOUT_SECONDS)))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return min(120.0, max(5.0, value))


def is_configured() -> bool:
    return bool(get_api_key())


def _safe_message(exc: Exception, api_key: str) -> str:
    """An exception summary that can never contain the key itself."""
    message = f"{type(exc).__name__}: {exc}"
    if api_key and api_key in message:
        message = message.replace(api_key, mask_key(api_key))
    return message[:500]


@lru_cache(maxsize=4)
def _get_client(api_key: str, base_url: str, timeout: float):
    # Imported lazily: the AI layer is optional, and importing the SDK must
    # never be a startup requirement for the rest of the API.
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
    """Generate plain text from Gemma 4 via DigitalOcean.

    `history` is an optional list of {"role": "user"|"assistant",
    "content": "..."} turns inserted before `user_prompt`.
    Raises ProviderNotConfigured, ProviderTimeout or ProviderError; callers
    decide what fallback or HTTP status to present. The API key is never
    included in an exception message or log line.
    """
    api_key = get_api_key()
    if not api_key:
        raise ProviderNotConfigured("DO_INFERENCE_API_KEY is not set")
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
        log.warning("AI request timed out (key %s, model %s)", mask_key(api_key), get_model())
        raise ProviderTimeout("AI request timed out") from exc
    except AuthenticationError as exc:
        log.warning("AI authentication failed (key %s): %s", mask_key(api_key), _safe_message(exc, api_key))
        raise ProviderError("AI provider rejected the credentials") from exc
    except PermissionDeniedError as exc:
        log.warning("AI permission denied (key %s): %s", mask_key(api_key), _safe_message(exc, api_key))
        raise ProviderError("AI provider denied access to the model") from exc
    except NotFoundError as exc:
        log.warning("AI model not found (model %s): %s", get_model(), _safe_message(exc, api_key))
        raise ProviderError("AI model is not available") from exc
    except RateLimitError as exc:
        log.warning("AI rate limit reached (key %s): %s", mask_key(api_key), _safe_message(exc, api_key))
        raise ProviderError("AI provider rate limit reached") from exc
    except BadRequestError as exc:
        log.warning("AI request rejected (model %s): %s", get_model(), _safe_message(exc, api_key))
        raise ProviderError("AI provider rejected the request") from exc
    except APIConnectionError as exc:
        log.warning("AI connection failed (key %s): %s", mask_key(api_key), _safe_message(exc, api_key))
        raise ProviderError("AI provider could not be reached") from exc
    except APIStatusError as exc:
        status = getattr(exc, "status_code", "?")
        log.warning(
            "AI provider error %s (key %s): %s",
            status,
            mask_key(api_key),
            _safe_message(exc, api_key),
        )
        raise ProviderError(f"AI provider error {status}") from exc
    except Exception as exc:  # any SDK-level failure, never a broken request
        log.warning("AI call failed (key %s): %s", mask_key(api_key), _safe_message(exc, api_key))
        raise ProviderError("AI provider call failed") from exc

    try:
        content = (response.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, TypeError) as exc:
        raise ProviderError("AI provider returned no choices") from exc
    if not content:
        raise ProviderError("AI provider returned an empty response")
    return content
