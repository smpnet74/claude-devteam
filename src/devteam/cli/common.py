"""Shared CLI utilities."""

from __future__ import annotations

from pathlib import Path


def get_devteam_home() -> Path:
    """Return the devteam home directory path (~/.devteam)."""
    return Path.home() / ".devteam"
