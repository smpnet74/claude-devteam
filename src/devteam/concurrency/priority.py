"""Priority levels for jobs and tasks.

Supports HIGH, NORMAL, LOW with comparison operators and FIFO
ordering within the same priority level.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class Priority(Enum):
    """Task/job priority levels. Higher value = higher priority."""

    HIGH = 3
    NORMAL = 2
    LOW = 1

    def to_int(self) -> int:
        return self.value

    @classmethod
    def from_string(cls, s: str) -> Priority:
        key = s.strip().upper()
        if key not in cls.__members__:
            raise ValueError(f"Invalid priority '{s}'. Must be one of: high, normal, low")
        return cls[key]

    @classmethod
    def default(cls) -> Priority:
        return cls.NORMAL

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Priority):
            return NotImplemented
        return self.value > other.value

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Priority):
            return NotImplemented
        return self.value >= other.value

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Priority):
            return NotImplemented
        return self.value < other.value

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Priority):
            return NotImplemented
        return self.value <= other.value


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
