"""Tests for DBOS/SQLite initialization."""

from pathlib import Path

from devteam.daemon.database import (
    DatabaseConfig,
    init_database,
)


class TestDatabaseConfig:
    def test_db_path_via_property(self, tmp_devteam_home: Path) -> None:
        config = DatabaseConfig(devteam_home=tmp_devteam_home)
        assert config.db_path == tmp_devteam_home / "devteam.sqlite"

    def test_db_url(self, tmp_devteam_home: Path) -> None:
        config = DatabaseConfig(devteam_home=tmp_devteam_home)
        assert config.db_url == f"sqlite:///{config.db_path}"


class TestDatabaseInit:
    def test_init_creates_config(self, tmp_devteam_home: Path) -> None:
        """init_database returns a valid DatabaseConfig."""
        config = init_database(tmp_devteam_home)
        assert isinstance(config, DatabaseConfig)
        assert config.devteam_home == tmp_devteam_home

    def test_init_idempotent(self, tmp_devteam_home: Path) -> None:
        """Calling init_database twice does not error."""
        config1 = init_database(tmp_devteam_home)
        config2 = init_database(tmp_devteam_home)
        assert config1.db_path == config2.db_path
