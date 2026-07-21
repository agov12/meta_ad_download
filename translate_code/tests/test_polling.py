"""Tests for poll_until_complete (providers/polling.py). No real long sleeps."""

import asyncio

import pytest

from ad_localizer.providers import polling
from ad_localizer.providers.polling import PollTimeoutError, poll_until_complete


async def test_completes_after_n_checks():
    calls = 0

    async def check() -> int:
        nonlocal calls
        calls += 1
        return calls

    result = await poll_until_complete(
        check,
        lambda n: n >= 3,
        timeout_s=5.0,
        initial_interval_s=0.001,
        max_interval_s=0.002,
    )
    assert result == 3
    assert calls == 3


async def test_returns_final_check_result():
    states = iter(["queued", "processing", "done"])

    async def check() -> str:
        return next(states)

    result = await poll_until_complete(
        check,
        lambda s: s == "done",
        timeout_s=5.0,
        initial_interval_s=0.001,
    )
    assert result == "done"


async def test_completes_immediately_without_sleeping(monkeypatch):
    async def no_sleep(seconds):  # pragma: no cover - should never run
        raise AssertionError(f"slept {seconds}s despite immediate completion")

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    async def check() -> str:
        return "done"

    assert await poll_until_complete(check, lambda s: s == "done") == "done"


async def test_backs_off_exponentially_up_to_max(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    calls = 0

    async def check() -> int:
        nonlocal calls
        calls += 1
        return calls

    await poll_until_complete(
        check,
        lambda n: n >= 5,
        timeout_s=1000.0,
        initial_interval_s=1.0,
        max_interval_s=4.0,
        backoff=2.0,
    )
    # 5 checks -> 4 sleeps: 1, 2, 4, then capped at max_interval_s
    assert sleeps == [1.0, 2.0, 4.0, 4.0]


async def test_raises_poll_timeout_error(monkeypatch):
    # Fake clock: sleeping advances monotonic time, so no real waiting happens.
    now = 0.0

    async def fake_sleep(seconds):
        nonlocal now
        now += seconds

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(polling.time, "monotonic", lambda: now)

    async def check() -> str:
        return "processing"

    with pytest.raises(PollTimeoutError) as excinfo:
        await poll_until_complete(
            check,
            lambda s: s == "done",
            timeout_s=10.0,
            initial_interval_s=1.0,
            backoff=1.5,
            describe="lipsync job",
        )
    assert "lipsync job" in str(excinfo.value)
    assert isinstance(excinfo.value, TimeoutError)


async def test_timeout_with_real_tiny_sleeps():
    async def check() -> str:
        return "processing"

    with pytest.raises(PollTimeoutError):
        await poll_until_complete(
            check,
            lambda s: s == "done",
            timeout_s=0.02,
            initial_interval_s=0.001,
            max_interval_s=0.005,
        )
