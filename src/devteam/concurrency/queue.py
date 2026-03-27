"""DBOS-compatible agent invocation queue.

All jobs submit to a single shared queue. The queue enforces a
concurrency limit (max_concurrent_agents from config.toml) and
dequeues by priority then FIFO.

In the full system this wraps DBOS Queue. For testability and
the plan implementation phase, we use a SQLite-backed queue with
the same semantics that will be swapped for DBOS Queue in
integration.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from devteam.concurrency.priority import Priority
from devteam.concurrency.rate_limit import is_paused

_INT_TO_PRIORITY = {p.to_int(): p for p in Priority}


# Queue item states
PENDING = "pending"
ACTIVE = "active"
COMPLETED = "completed"
FAILED = "failed"


@dataclass
class AgentQueueConfig:
    """Configuration for the agent invocation queue."""

    max_concurrent: int

    def __post_init__(self) -> None:
        if self.max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")


@dataclass
class AgentQueueItem:
    """An item in the agent invocation queue."""

    id: int
    job_id: str
    task_id: str
    role: str
    priority: Priority
    status: str
    enqueued_at: float

    def mark_complete(self, conn: sqlite3.Connection) -> None:
        """Mark this queue item as completed, freeing the concurrency slot.

        Only transitions from ACTIVE status. Raises RuntimeError if the
        item is not currently active (e.g., already completed or failed).
        """
        cursor = conn.execute(
            "UPDATE agent_queue SET status = ?, completed_at = ? WHERE id = ? AND status = ?",
            (COMPLETED, time.time(), self.id, ACTIVE),
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise RuntimeError(
                f"Queue item {self.id} is not active; cannot mark as completed"
            )

    def mark_failed(self, conn: sqlite3.Connection) -> None:
        """Mark this queue item as failed, freeing the concurrency slot.

        Only transitions from ACTIVE status. Raises RuntimeError if the
        item is not currently active (e.g., already completed or failed).
        """
        cursor = conn.execute(
            "UPDATE agent_queue SET status = ?, completed_at = ? WHERE id = ? AND status = ?",
            (FAILED, time.time(), self.id, ACTIVE),
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise RuntimeError(
                f"Queue item {self.id} is not active; cannot mark as failed"
            )


def create_agent_queue_config(max_concurrent: int = 3) -> AgentQueueConfig:
    """Create queue configuration. Default matches spec: 3 concurrent agents."""
    return AgentQueueConfig(max_concurrent=max_concurrent)


def init_queue_table(conn: sqlite3.Connection) -> None:
    """Create the agent_queue table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            role TEXT NOT NULL,
            priority INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            enqueued_at REAL NOT NULL,
            started_at REAL,
            completed_at REAL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_queue_status
        ON agent_queue (status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_queue_priority
        ON agent_queue (priority DESC, enqueued_at ASC)
    """)
    conn.commit()


def enqueue_agent_invocation(
    conn: sqlite3.Connection,
    job_id: str,
    task_id: str,
    role: str,
    priority: Priority,
) -> int:
    """Add an agent invocation to the queue. Returns the queue item ID."""
    cursor = conn.execute(
        """
        INSERT INTO agent_queue (job_id, task_id, role, priority, status, enqueued_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (job_id, task_id, role, priority.to_int(), PENDING, time.time()),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def dequeue_next(
    conn: sqlite3.Connection,
    max_concurrent: int,
) -> AgentQueueItem | None:
    """Dequeue the highest-priority pending item, respecting concurrency limit.

    Uses BEGIN IMMEDIATE to hold a write lock for the entire
    check-then-act sequence, preventing races between concurrent
    dequeue callers.

    Returns None if no items are pending or the concurrency limit is reached.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        if is_paused(conn):
            conn.rollback()
            return None

        active = conn.execute(
            "SELECT COUNT(*) FROM agent_queue WHERE status = ?", (ACTIVE,)
        ).fetchone()[0]
        if active >= max_concurrent:
            conn.rollback()
            return None

        row = conn.execute(
            "SELECT id, job_id, task_id, role, priority, status, enqueued_at "
            "FROM agent_queue WHERE status = ? "
            "ORDER BY priority DESC, enqueued_at ASC, id ASC LIMIT 1",
            (PENDING,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return None

        conn.execute(
            "UPDATE agent_queue SET status = ?, started_at = ? WHERE id = ?",
            (ACTIVE, time.time(), row[0]),
        )
        conn.commit()

        return AgentQueueItem(
            id=row[0],
            job_id=row[1],
            task_id=row[2],
            role=row[3],
            priority=_INT_TO_PRIORITY[row[4]],
            status=ACTIVE,
            enqueued_at=row[6],
        )
    except Exception:
        conn.rollback()
        raise


def get_queue_depth(conn: sqlite3.Connection) -> int:
    """Count of pending items in the queue."""
    row = conn.execute(
        "SELECT COUNT(*) FROM agent_queue WHERE status = ?",
        (PENDING,),
    ).fetchone()
    return row[0] if row else 0


def get_active_count(conn: sqlite3.Connection) -> int:
    """Count of currently active (running) items."""
    row = conn.execute(
        "SELECT COUNT(*) FROM agent_queue WHERE status = ?",
        (ACTIVE,),
    ).fetchone()
    return row[0] if row else 0
