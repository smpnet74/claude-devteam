"""Shared DBOS test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def dbos_db_path(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'dbos_test.sqlite'}"


@pytest.fixture
def dbos_launch(dbos_db_path: str):
    from dbos import DBOS

    DBOS(config={"name": "devteam_test", "system_database_url": dbos_db_path})
    DBOS.launch()
    yield DBOS
    DBOS.destroy()
