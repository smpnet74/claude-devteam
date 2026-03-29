"""CLI priority commands for devteam prioritize and --priority flag.

These are the business logic functions called by the CLI layer.
The actual Click/Typer command definitions live in the CLI module
and delegate to these functions.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from devteam.concurrency.priority import Priority

PENDING = "pending"  # Queue status constant (was in queue.py, now local)


@dataclass
class PrioritizeResult:
    """Result of a prioritize operation."""

    success: bool
    message: str
    new_priority: Priority | None = None


def prioritize_task(
    db: sqlite3.Connection,
    job_id: str,
    task_id: str,
    priority: Priority,
) -> PrioritizeResult:
    """Update the priority of a queued task.

    Only affects pending (not yet active) tasks.

    Args:
        db: SQLite connection.
        job_id: Job identifier (e.g., "W-1").
        task_id: Task identifier (e.g., "T-3").
        priority: New priority level.

    Returns:
        PrioritizeResult with success status and message.
    """
    # Atomic SELECT-then-UPDATE: BEGIN IMMEDIATE acquires a write lock
    # so no other connection can modify the row between our check and update.
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            """
            SELECT id, status FROM agent_queue
            WHERE job_id = ? AND task_id = ? AND status = ?
            """,
            (job_id, task_id, PENDING),
        ).fetchone()

        if row is None:
            # Check if it exists at all
            exists = db.execute(
                "SELECT id, status FROM agent_queue WHERE job_id = ? AND task_id = ?",
                (job_id, task_id),
            ).fetchone()
            db.rollback()
            if exists is None:
                return PrioritizeResult(
                    success=False,
                    message=f"Task {job_id}/{task_id} not found in queue.",
                )
            return PrioritizeResult(
                success=False,
                message=(
                    f"Task {job_id}/{task_id} is {exists[1]}, can only prioritize pending tasks."
                ),
            )

        db.execute(
            "UPDATE agent_queue SET priority = ? WHERE id = ? AND status = ?",
            (priority.to_int(), row[0], PENDING),
        )
        db.commit()

        return PrioritizeResult(
            success=True,
            message=f"Task {job_id}/{task_id} priority set to {priority.name.lower()}.",
            new_priority=priority,
        )
    except Exception:
        db.rollback()
        raise


def parse_priority_flag(value: str | None) -> Priority:
    """Parse the --priority CLI flag value.

    Returns Priority.NORMAL if value is None (not specified).

    Raises:
        ValueError: If value is not a valid priority string.
    """
    if value is None:
        return Priority.default()
    return Priority.from_string(value)
