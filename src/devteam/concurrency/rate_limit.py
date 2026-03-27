"""Rate limit detection and global pause flag.

The global pause flag is a single row in SQLite that all workflows check
before dispatching agent invocations. When any workflow hits a rate limit,
it sets the flag so all workflows pause together.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass


DEFAULT_BACKOFF_SECONDS = 1800  # 30 minutes, from config.toml default


@dataclass
class PauseStatus:
    """Current state of the global pause flag."""

    resume_at: float  # unix timestamp
    reason: str = "rate_limit"

    def remaining_seconds(self) -> float:
        return max(0.0, self.resume_at - time.time())

    def is_expired(self) -> bool:
        return time.time() >= self.resume_at


@dataclass
class PauseCheckResult:
    """Result of checking the pause flag before an invocation."""

    paused: bool
    resume_at: float | None = None


def init_pause_table(conn: sqlite3.Connection) -> None:
    """Create the global_pause table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS global_pause (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            resume_at REAL NOT NULL,
            set_at REAL NOT NULL,
            reason TEXT
        )
    """)
    conn.commit()


def set_global_pause(
    conn: sqlite3.Connection,
    seconds: float,
    reason: str = "rate_limit",
) -> float:
    """Set the global pause flag. Returns the resume_at timestamp.

    Uses INSERT OR REPLACE to ensure only one row exists (id=1).
    """
    now = time.time()
    resume_at = now + seconds
    # Only update if new resume_at is later than the existing one (monotonic).
    existing = conn.execute(
        "SELECT resume_at FROM global_pause WHERE id = 1"
    ).fetchone()
    if existing and existing[0] >= resume_at:
        # Existing pause extends further; keep it.
        conn.commit()
        return existing[0]
    conn.execute(
        """
        INSERT OR REPLACE INTO global_pause (id, resume_at, set_at, reason)
        VALUES (1, ?, ?, ?)
        """,
        (resume_at, now, reason),
    )
    conn.commit()
    return resume_at


def get_global_pause(conn: sqlite3.Connection) -> PauseStatus | None:
    """Get the current pause status. Returns None if not paused or expired."""
    row = conn.execute("SELECT resume_at, reason FROM global_pause WHERE id = 1").fetchone()
    if row is None:
        return None
    status = PauseStatus(resume_at=row[0], reason=row[1] or "rate_limit")
    if status.is_expired():
        # Auto-clear expired pauses
        clear_global_pause(conn)
        return None
    return status


def clear_global_pause(conn: sqlite3.Connection) -> None:
    """Clear the global pause flag."""
    conn.execute("DELETE FROM global_pause WHERE id = 1")
    conn.commit()


def is_paused(conn: sqlite3.Connection) -> bool:
    """Check if the system is currently paused."""
    return get_global_pause(conn) is not None


def check_pause_before_invoke(conn: sqlite3.Connection) -> PauseCheckResult:
    """Check the pause flag before dispatching an agent invocation.

    This is called by every workflow before each agent invocation.
    When one workflow sets the pause flag, all workflows see it.
    """
    pause = get_global_pause(conn)
    if pause is None:
        return PauseCheckResult(paused=False)
    return PauseCheckResult(paused=True, resume_at=pause.resume_at)


def _parse_reset_seconds(error_message: str) -> int | None:
    """Extract the reset/retry time in seconds from a rate limit error.

    Handles formats:
        - "Retry after 1800 seconds"
        - "retry-after: 120"
        - "Retry after 1800 seconds."
    """
    msg = error_message
    # Pattern: "Retry after N seconds"
    match = re.search(r"[Rr]etry\s+after\s+(\d+)\s+seconds", msg)
    if match:
        return int(match.group(1))
    # Pattern: "retry-after: N"
    match = re.search(r"retry-after:\s*(\d+)", msg, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def handle_rate_limit_error(
    conn: sqlite3.Connection,
    error: Exception,
    default_backoff: int = DEFAULT_BACKOFF_SECONDS,
) -> int:
    """Handle a rate limit error by setting the global pause flag.

    Parses the error message to extract the retry time. Falls back to
    default_backoff (which should come from config) if the error message
    can't be parsed.

    Returns the number of seconds to wait.
    """
    parsed = _parse_reset_seconds(str(error))
    seconds = parsed if parsed is not None else default_backoff
    set_global_pause(conn, seconds=seconds, reason="rate_limit")
    return seconds
