"""Durable sleep that persists across process restarts.

Stores the wake time in the same SQLite database as the rate limit
coordinator. On restart, checks for any pending sleep and resumes
with the remaining duration.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from devteam.concurrency.rate_limit import (
    clear_global_pause,
    get_global_pause,
    init_pause_table,
    set_global_pause,
)


@dataclass(frozen=True)
class PendingSleep:
    """A sleep that was persisted and may need to be resumed."""

    resume_at: float
    reason: str

    def remaining_seconds(self) -> float:
        """Seconds remaining until wake time. Returns 0 if already expired."""
        return max(0.0, self.resume_at - time.time())

    def is_expired(self) -> bool:
        return time.time() >= self.resume_at


def durable_sleep(
    conn: sqlite3.Connection,
    duration_seconds: float,
    reason: str = "rate_limit",
    sleep_fn: Callable[[float], None] | None = None,
) -> None:
    """Sleep for a duration, persisting the wake time in SQLite.

    If the process crashes and restarts, check_pending_sleep() can
    detect and resume the remaining sleep.

    Args:
        conn: SQLite connection (with pause table initialized).
        duration_seconds: How long to sleep.
        reason: Why we're sleeping (stored in the pause record).
        sleep_fn: Optional callable for testing (default: time.sleep).
    """
    if sleep_fn is None:
        sleep_fn = time.sleep

    set_global_pause(conn, seconds=duration_seconds, reason=reason)
    try:
        sleep_fn(duration_seconds)
    finally:
        clear_global_pause(conn)


def check_pending_sleep(conn: sqlite3.Connection) -> PendingSleep | None:
    """Check if there's a pending sleep to resume after restart.

    Call this on startup. If a pending sleep is found, the caller
    should sleep for the remaining duration.

    Returns:
        PendingSleep if a non-expired pause exists, None otherwise.
    """
    init_pause_table(conn)
    pause = get_global_pause(conn)
    if pause is None:
        return None
    return PendingSleep(
        resume_at=pause.resume_at,
        reason=pause.reason,
    )


def resume_sleep(
    conn: sqlite3.Connection,
    pending: PendingSleep,
    sleep_fn: Callable[[float], None] | None = None,
) -> None:
    """Resume a pending sleep for its remaining duration.

    Args:
        conn: SQLite connection.
        pending: The pending sleep to resume.
        sleep_fn: Optional callable for testing (default: time.sleep).
    """
    if sleep_fn is None:
        sleep_fn = time.sleep

    remaining = pending.remaining_seconds()
    try:
        if remaining > 0:
            sleep_fn(remaining)
    finally:
        clear_global_pause(conn)


def cancel_sleep(conn: sqlite3.Connection) -> None:
    """Cancel any pending sleep by clearing the pause flag."""
    clear_global_pause(conn)
