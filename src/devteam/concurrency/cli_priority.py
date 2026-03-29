"""CLI priority commands for devteam --priority flag.

Provides parse_priority_flag for CLI argument validation.
"""

from __future__ import annotations

from devteam.concurrency.priority import Priority


def parse_priority_flag(value: str | None) -> Priority:
    """Parse the --priority CLI flag value.

    Returns Priority.NORMAL if value is None (not specified).

    Raises:
        ValueError: If value is not a valid priority string.
    """
    if value is None:
        return Priority.default()
    return Priority.from_string(value)
