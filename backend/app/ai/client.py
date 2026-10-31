"""One place for Claude calls (Backend-owned).

Every AI feature has a non-AI fallback, so the app works with no API key, a slow network or a
refusal. Callers catch AIUnavailable and use their fallback. Claude never decides a route: it
classifies photos and writes plain-language text; routing math stays in backend/app/routing/.
"""
import logging
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from ..config import ANTHROPIC_API_KEY, CLAUDE_MODEL

log = logging.getLogger("wheelio.ai")
T = TypeVar("T", bound=BaseModel)

_client: anthropic.Anthropic | None = None


class AIUnavailable(Exception):
    """No key, network or API error, refusal, or unusable output. Use the fallback."""


def available() -> bool:
    return bool(ANTHROPIC_API_KEY)


def _get() -> anthropic.Anthropic:
    global _client
    if not available():
        raise AIUnavailable("ANTHROPIC_API_KEY is not set")
    if _client is None:
        # Short timeout: a person is waiting on a phone. The SDK retries 429/5xx twice.
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=30.0, max_retries=2)
    return _client


def structured(system: str, content: list[dict] | str, schema: type[T], effort: str = "low",
               max_tokens: int = 4000) -> T:
    """One Claude call whose answer must match `schema`. Raises AIUnavailable on any failure."""
    try:
        response = _get().beta.messages.parse(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
            output_config={"effort": effort},
            # If a safety classifier declines, the API retries on a fallback model in the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIConnectionError as e:
        raise AIUnavailable(f"network: {e}") from e
    except anthropic.APIStatusError as e:
        raise AIUnavailable(f"API {e.status_code}: {e.message}") from e
    if response.stop_reason == "refusal":
        raise AIUnavailable("refused")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise AIUnavailable(f"unusable output ({response.stop_reason})")
    log.info("claude ok: %s in=%s out=%s", schema.__name__, response.usage.input_tokens,
             response.usage.output_tokens)
    return response.parsed_output
