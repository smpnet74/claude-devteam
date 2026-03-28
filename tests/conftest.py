"""Shared pytest fixtures for devteam tests."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_devteam_home(tmp_path: Path) -> Path:
    """Create a temporary ~/.devteam directory structure."""
    home = tmp_path / ".devteam"
    home.mkdir()
    (home / "logs").mkdir()
    (home / "traces").mkdir()
    (home / "exports").mkdir()
    (home / "focus").mkdir()
    (home / "agents").mkdir()
    (home / "projects").mkdir()
    (home / "knowledge").mkdir()
    return home


@pytest.fixture
def tmp_project_dir(tmp_path: Path) -> Path:
    """Create a temporary project directory."""
    project = tmp_path / "myproject"
    project.mkdir()
    return project


from tests.conftest_dbos import dbos_db_path, dbos_launch  # noqa: F401
