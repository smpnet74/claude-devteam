"""Tests for the CLI interface."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from devteam.cli.main import app

runner = CliRunner()


class TestCLIHelp:
    def test_main_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "devteam" in result.output.lower() or "Durable" in result.output

    def test_init_help(self) -> None:
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0

    def test_daemon_help(self) -> None:
        result = runner.invoke(app, ["daemon", "--help"])
        assert result.exit_code == 0

    def test_project_help(self) -> None:
        result = runner.invoke(app, ["project", "--help"])
        assert result.exit_code == 0


class TestInitCommand:
    def test_init_creates_directory_structure(self, tmp_path: Path) -> None:
        devteam_home = tmp_path / ".devteam"
        with patch("devteam.cli.commands.init_cmd.get_devteam_home", return_value=devteam_home):
            result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert devteam_home.exists()
        assert (devteam_home / "config.toml").exists()
        assert (devteam_home / "logs").is_dir()
        assert (devteam_home / "traces").is_dir()
        assert (devteam_home / "exports").is_dir()
        assert (devteam_home / "focus").is_dir()
        assert (devteam_home / "agents").is_dir()
        assert (devteam_home / "projects").is_dir()
        assert (devteam_home / "knowledge").is_dir()

    def test_init_idempotent(self, tmp_path: Path) -> None:
        devteam_home = tmp_path / ".devteam"
        with patch("devteam.cli.commands.init_cmd.get_devteam_home", return_value=devteam_home):
            runner.invoke(app, ["init"])
            result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "already" in result.output.lower() or result.exit_code == 0


class TestDaemonCommands:
    def test_daemon_status_not_running(self, tmp_path: Path) -> None:
        devteam_home = tmp_path / ".devteam"
        devteam_home.mkdir()
        with patch(
            "devteam.cli.commands.daemon_cmd.get_devteam_home", return_value=devteam_home
        ):
            result = runner.invoke(app, ["daemon", "status"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()

    def test_daemon_stop_not_running(self, tmp_path: Path) -> None:
        devteam_home = tmp_path / ".devteam"
        devteam_home.mkdir()
        with patch(
            "devteam.cli.commands.daemon_cmd.get_devteam_home", return_value=devteam_home
        ):
            result = runner.invoke(app, ["daemon", "stop"])
        assert result.exit_code == 1


class TestProjectCommands:
    def test_project_add_stub(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["project", "add", str(tmp_path)])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_project_remove_stub(self) -> None:
        result = runner.invoke(app, ["project", "remove", "myapp"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()
