"""Tests for the CLI interface."""

from pathlib import Path
from unittest.mock import patch

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
        assert "already" in result.output.lower()


class TestProjectCommands:
    def test_project_add_copies_agents(self, tmp_path: Path) -> None:
        devteam_home = tmp_path / "devteam_home"
        agents_dir = devteam_home / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "ceo.md").write_text("---\nmodel: opus\ntools:\n  - Read\n---\nCEO prompt")

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("devteam.cli.commands.project_cmd.get_devteam_home", return_value=devteam_home):
            result = runner.invoke(app, ["project", "add", str(project_dir)])
        assert result.exit_code == 0
        assert "Copied" in result.output
        assert (project_dir / ".claude" / "agents" / "ceo.md").exists()

    def test_project_add_nonexistent_dir(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["project", "add", str(tmp_path / "nonexistent")])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_project_add_no_init(self, tmp_path: Path) -> None:
        devteam_home = tmp_path / "devteam_home"
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("devteam.cli.commands.project_cmd.get_devteam_home", return_value=devteam_home):
            result = runner.invoke(app, ["project", "add", str(project_dir)])
        assert result.exit_code == 1
        assert "devteam init" in result.output.lower()

    def test_project_remove_stub(self) -> None:
        result = runner.invoke(app, ["project", "remove", "myapp"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()


class TestFocusCommand:
    def test_focus_set(self) -> None:
        result = runner.invoke(app, ["focus", "W-1"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_focus_clear(self) -> None:
        result = runner.invoke(app, ["focus", "--clear"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_focus_show(self) -> None:
        result = runner.invoke(app, ["focus"])
        assert result.exit_code == 0
