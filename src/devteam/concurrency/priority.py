"""Priority levels for jobs and tasks.

Re-exports the canonical Priority enum from models.entities so that
all code uses a single enum with string values and integer ordering.
"""

from __future__ import annotations

from typing import Any

from devteam.models.entities import Priority

__all__ = ["Priority", "prioritize_tasks"]


def prioritize_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort tasks by priority (descending), then by enqueued_at (ascending, FIFO).

    Args:
        tasks: List of task dicts with at least a "priority" key (Priority enum).
               Optional "enqueued_at" key (numeric timestamp) for FIFO within
               same priority.

    Returns:
        New sorted list. Original list is not modified.
    """
    return sorted(
        tasks,
        key=lambda t: (
            -t["priority"].to_int(),
            t.get("enqueued_at", 0),
        ),
    )
