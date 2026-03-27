"""Rate-limit-aware agent invocation wrapper.

Wraps agent SDK calls with:
1. Pre-invocation pause check (respect global pause from other workflows)
2. RateLimitError catch with parse, pause, sleep, retry
3. Post-retry pause clear

In the full system, sleep_fn maps to DBOS.sleep() for durable sleep.
For testing, sleep_fn is injectable.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import sqlite3

from devteam.concurrency.rate_limit import (
    check_pause_before_invoke,
    handle_rate_limit_error,
    clear_global_pause,
)


class RateLimitError(Exception):
    """Raised when the Agent SDK hits an API rate limit."""

    pass


def _default_sleep(seconds: float) -> None:
    """Default sleep function. Replaced by DBOS.sleep() in production."""
    time.sleep(seconds)


def rate_limit_aware_invoke(
    db: sqlite3.Connection,
    invoke_fn: Callable[..., Any],
    role: str,
    task_id: str,
    context: str,
    sleep_fn: Callable[[float], None] | None = None,
) -> Any:
    """Invoke an agent with rate limit awareness.

    1. Check if globally paused (another workflow hit a limit) — if so, wait.
    2. Call invoke_fn.
    3. On RateLimitError: set global pause, sleep, clear pause, retry once.

    Args:
        db: SQLite connection for pause flag reads/writes.
        invoke_fn: The actual agent invocation function (Agent SDK query).
        role: Agent role being invoked.
        task_id: Task identifier for logging.
        context: The prompt/context to send to the agent.
        sleep_fn: Injectable sleep function. Defaults to time.sleep.
                  In production, this is DBOS.sleep() for durable sleep.

    Returns:
        The result from invoke_fn.
    """
    if sleep_fn is None:
        sleep_fn = _default_sleep

    # Step 1: Check if we're already paused by another workflow
    pause_check = check_pause_before_invoke(db)
    if pause_check.paused and pause_check.resume_at is not None:
        wait_seconds = max(0, pause_check.resume_at - time.time())
        sleep_fn(wait_seconds)

    # Step 2: Try the invocation
    try:
        result = invoke_fn(role=role, task_id=task_id, context=context)
        return result
    except RateLimitError as e:
        # Step 3: Set global pause, sleep, clear, retry
        seconds = handle_rate_limit_error(db, e)
        sleep_fn(seconds)
        clear_global_pause(db)
        result = invoke_fn(role=role, task_id=task_id, context=context)
        return result
