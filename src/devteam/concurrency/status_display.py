"""Status display formatting for rate limits and queue state.

Used by `devteam status` to show rate limit state (conditional --
only when a pause is active) and agent concurrency counts.
"""

from __future__ import annotations

import sqlite3

from devteam.concurrency.rate_limit import get_global_pause
from devteam.concurrency.queue import get_active_count


def _format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if remaining_minutes == 0:
        return f"{hours}h"
    return f"{hours}h {remaining_minutes}m"


def format_rate_limit_status(db: sqlite3.Connection) -> str | None:
    """Format rate limit status for display.

    Returns None if not paused (conditional display -- only shown when active).
    Returns a formatted string like "Rate limited -- resumes in 1h 42m" when paused.
    """
    pause = get_global_pause(db)
    if pause is None:
        return None
    remaining = pause.remaining_seconds()
    duration = _format_duration(remaining)
    return f"Rate limited \u2014 resumes in {duration}"


def format_queue_status(db: sqlite3.Connection, max_concurrent: int) -> str:
    """Format queue/concurrency status for display.

    Always shown: "Agents running: N/M"
    """
    active = get_active_count(db)
    return f"Agents running: {active}/{max_concurrent}"
