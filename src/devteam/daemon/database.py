"""DBOS/SQLite database initialization and configuration.

DBOS uses SQLite for durable workflow state persistence. The database
lives at ~/.devteam/devteam.sqlite. This module handles configuration
and initialization — actual DBOS workflow registration happens in the
workflow modules (Plans 2+).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class DatabaseConfig:
    """Configuration for the DBOS SQLite database."""

    devteam_home: Path

    @property
    def db_path(self) -> Path:
        return self.devteam_home / "devteam.sqlite"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


def init_database(devteam_home: Path) -> DatabaseConfig:
    """Initialize the database configuration.

    In Plan 2 (Agent Invocation), this will also initialize DBOS with:
      - DBOS.launch() for workflow engine startup
      - Table creation for entity state tracking
      - WAL mode for crash resilience

    For now, returns the configuration that the daemon will use.
    """
    config = DatabaseConfig(devteam_home=devteam_home)

    # Ensure the parent directory exists
    config.db_path.parent.mkdir(parents=True, exist_ok=True)

    return config
