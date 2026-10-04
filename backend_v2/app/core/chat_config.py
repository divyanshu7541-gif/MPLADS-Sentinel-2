"""
Secure configuration for the optional AI chat provider.

The API key is NEVER hardcoded, never logged in full, and never sent to
the frontend. It is read at request time from the process environment
(`GEMINI_API_KEY`), which is the only source of truth in production.

For local development convenience, if the environment variable isn't
set, we also look for a `.env` file at the repository root and parse it
ourselves with the standard library (no extra dependency). This file is
already covered by `.gitignore` (`.env`, `.env.*`, except `.env.example`),
so a locally-set key is never committed.

To enable the AI-powered chatbot:
  1. Copy `.env.example` (repo root) to `.env`.
  2. Set GEMINI_API_KEY=your-key-here in `.env`.
  3. Restart the backend and check the startup log line beginning
     "[SENTINEL:CHAT]" -- it reports whether a key was found, without
     ever printing the key itself.

We deliberately do NOT validate the key's shape/prefix/length here --
Google issues more than one credential format for the Generative
Language API, and hard-coding today's pattern only risks rejecting a
perfectly valid key tomorrow. Any non-empty value is treated as
"configured" and handed to Google's API, which is the actual source of
truth on whether it's valid.

Without a (working) key configured, the chat endpoint still works using
a built-in, rule-based navigation guide -- it just won't have free-form
AI answers.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]  # backend_v2/app/core -> repo root
_ENV_FILE = _REPO_ROOT / ".env"


def _parse_env_file(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    except OSError:
        pass
    return values


@lru_cache(maxsize=1)
def _dotenv_values() -> dict:
    return _parse_env_file(_ENV_FILE)


def get_setting(name: str, default: str = "") -> str:
    """Read a setting from the real environment first, falling back to a
    local .env file for development convenience. Never raises, never logs
    the value."""
    value = os.environ.get(name)
    if value:
        return value
    return _dotenv_values().get(name, default)


def get_gemini_api_key() -> str:
    return get_setting("GEMINI_API_KEY", "")


def get_gemini_model() -> str:
    # `gemini-flash-latest` is Google's officially documented rolling alias:
    # it always points at the current recommended Flash model and is
    # hot-swapped by Google as new versions ship, so this default doesn't
    # go stale the way a pinned model id (e.g. the old `gemini-2.0-flash`,
    # which Google retired on 2026-06-01) eventually does.
    return get_setting("GEMINI_MODEL", "gemini-flash-latest")


def get_fallback_models() -> list:
    """Secondary models tried, in order, ONLY when the configured/default
    model comes back as literally not-found (HTTP 404 -- e.g. a pinned
    model id that Google has since retired). Never used for auth, quota,
    or network failures, so a real misconfiguration is never masked by a
    model swap that silently "fixes" the symptom without fixing the cause.
    """
    primary = get_gemini_model()
    candidates = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-3.6-flash"]
    return [m for m in candidates if m != primary]


def is_ai_configured() -> bool:
    return bool(get_gemini_api_key())


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}...{key[-4:]}"


class ChatConfigStatus(NamedTuple):
    configured: bool
    source: Optional[str]  # "environment" | ".env file" | None
    masked_key: str
    key_length: int


def describe_chat_config() -> ChatConfigStatus:
    """Full diagnostic snapshot for startup logging / debugging.
    Safe to print: never includes the raw key."""
    env_value = os.environ.get("GEMINI_API_KEY", "")
    if env_value:
        key, source = env_value, "environment"
    else:
        key, source = _dotenv_values().get("GEMINI_API_KEY", ""), ".env file"

    if not key:
        return ChatConfigStatus(False, None, "", 0)

    return ChatConfigStatus(
        configured=True,
        source=source,
        masked_key=mask_key(key),
        key_length=len(key),
    )
