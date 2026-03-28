"""Workflow event types and formatters for terminal rendering.

Events are set on DBOS workflows via set_event() and polled by the
terminal UI via get_all_events_async(). This module defines the event
data structures and formatting logic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class EventLevel(str, Enum):
    """Severity level for log events."""

    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    QUESTION = "question"
    SUCCESS = "success"


@dataclass(frozen=True)
class LogEvent:
    """A single log event emitted by a workflow."""

    message: str
    level: EventLevel
    seq: int
    timestamp: float = field(default_factory=time.time)


def make_log_key(seq: int) -> str:
    """Create a DBOS event key for a log entry.

    Keys are zero-padded to 6 digits so lexicographic sort == numeric sort.
    """
    return f"log:{seq:06d}"


def format_log_event(
    event: LogEvent,
    job_id: str,
    task_id: str | None = None,
) -> str:
    """Format a log event as a terminal display line."""
    prefix = f"[{job_id}/{task_id}]" if task_id else f"[{job_id}]"

    if event.level == EventLevel.QUESTION:
        return f"{prefix} QUESTION {event.message}"
    if event.level == EventLevel.ERROR:
        return f"{prefix} ERROR {event.message}"
    if event.level == EventLevel.WARN:
        return f"{prefix} WARN {event.message}"
    return f"{prefix} {event.message}"
