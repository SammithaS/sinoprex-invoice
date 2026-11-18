"""Retries of transient LLM provider errors (app/llm/retry.py)."""

import pytest

from app.llm.retry import is_transient, retry_transient


class Flaky:
    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


async def test_transient_errors_are_retried() -> None:
    call = Flaky([RuntimeError("503 UNAVAILABLE"), RuntimeError("429 RESOURCE_EXHAUSTED")])
    assert await retry_transient(call, delays=(0, 0)) == "ok"
    assert call.calls == 3


async def test_gives_up_after_the_last_delay() -> None:
    call = Flaky([RuntimeError("503")] * 5)
    with pytest.raises(RuntimeError, match="503"):
        await retry_transient(call, delays=(0, 0))
    assert call.calls == 3


async def test_other_errors_are_not_retried() -> None:
    call = Flaky([ValueError("schema mismatch")])
    with pytest.raises(ValueError):
        await retry_transient(call, delays=(0, 0))
    assert call.calls == 1


def test_transient_detection() -> None:
    assert is_transient(RuntimeError("Error calling model (RESOURCE_EXHAUSTED): 429"))
    assert is_transient(RuntimeError("This model is currently overloaded"))
    assert not is_transient(KeyError("policy"))
