"""Shared polling helper for long-running vendor jobs (sync.so, Vozo).

Every long-running API in this codebase is async-with-polling. Use this
helper instead of hand-rolling sleep loops so backoff and timeouts are
consistent.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class PollTimeoutError(TimeoutError):
    """Raised when a polled job does not reach a terminal state in time."""


async def poll_until_complete(
    check: Callable[[], Awaitable[T]],
    is_done: Callable[[T], bool],
    *,
    timeout_s: float = 900.0,
    initial_interval_s: float = 2.0,
    max_interval_s: float = 30.0,
    backoff: float = 1.5,
    describe: str = "job",
) -> T:
    """Call ``check`` until ``is_done(result)`` is true.

    Backs off exponentially from ``initial_interval_s`` to ``max_interval_s``.
    Raises PollTimeoutError after ``timeout_s`` seconds of waiting.
    Returns the final result from ``check``.
    """
    deadline = time.monotonic() + timeout_s
    interval = initial_interval_s
    while True:
        result = await check()
        if is_done(result):
            return result
        if time.monotonic() >= deadline:
            raise PollTimeoutError(
                f"{describe} did not complete within {timeout_s:.0f}s"
            )
        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        interval = min(interval * backoff, max_interval_s)
