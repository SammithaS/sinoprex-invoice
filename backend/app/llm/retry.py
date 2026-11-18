"""Retry transient LLM provider errors (503 overloaded, 429 rate-limited) with back-off.

Each model call already has the SDK's own retries and a provider fallback chain; this is the
outer layer for the moments when *every* provider is briefly unavailable (seen in the Phase 11
live eval: 9 of 25 cases needed a manual rerun). Anything else, including a daily quota that
stays exhausted after the last attempt, is re-raised unchanged so the case fails visibly.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

log = logging.getLogger(__name__)

TRANSIENT = ("503", "429", "unavailable", "resource_exhausted", "overloaded", "rate limit")


def is_transient(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in TRANSIENT)


async def retry_transient[T](
    call: Callable[[], Awaitable[T]],
    *,
    delays: Sequence[float] = (8, 30),
    label: str = "LLM call",
) -> T:
    """Run `call`; on a transient provider error wait `delays[i]` seconds and try again."""
    for attempt, delay in enumerate([*delays, None], start=1):
        try:
            return await call()
        except Exception as exc:
            if delay is None or not is_transient(exc):
                raise
            log.warning("%s failed (attempt %d): %s — retrying in %ss", label, attempt, exc, delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
