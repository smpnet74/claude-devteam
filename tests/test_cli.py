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
        with patch("devteam.cli.commands.daemon_cmd.get_devteam_home", return_value=devteam_home):
            result = runner.invoke(app, ["daemon", "status"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()

    def test_daemon_stop_not_running(self, tmp_path: Path) -> None:
        devteam_home = tmp_path / ".devteam"
        devteam_home.mkdir()
        with patch("devteam.cli.commands.daemon_cmd.get_devteam_home", return_value=devteam_home):
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


class TestJobCommands:
    def test_start_help(self) -> None:
        result = runner.invoke(app, ["start", "--help"])
        assert result.exit_code == 0
        assert "spec" in result.output.lower() or "plan" in result.output.lower()

    def test_start_stub(self) -> None:
        result = runner.invoke(app, ["start", "--spec", "/tmp/spec.md", "--plan", "/tmp/plan.md"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_start_with_prompt(self) -> None:
        result = runner.invoke(app, ["start", "--prompt", "Fix the bug"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_start_with_issue(self) -> None:
        result = runner.invoke(app, ["start", "--issue", "https://github.com/org/repo/issues/42"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_status_stub(self) -> None:
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0

    def test_status_with_job_id(self) -> None:
        result = runner.invoke(app, ["status", "W-1"])
        assert result.exit_code == 0

    def test_status_with_task_id(self) -> None:
        result = runner.invoke(app, ["status", "W-1/T-3"])
        assert result.exit_code == 0

    def test_status_questions_flag(self) -> None:
        result = runner.invoke(app, ["status", "--questions"])
        assert result.exit_code == 0

    def test_stop_stub(self) -> None:
        result = runner.invoke(app, ["stop"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_stop_with_job_id(self) -> None:
        result = runner.invoke(app, ["stop", "W-1"])
        assert result.exit_code == 0

    def test_pause_stub(self) -> None:
        result = runner.invoke(app, ["pause", "W-1"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_resume_stub(self) -> None:
        result = runner.invoke(app, ["resume", "W-1"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_cancel_stub(self) -> None:
        result = runner.invoke(app, ["cancel", "W-1"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_cancel_revert_merged(self) -> None:
        result = runner.invoke(app, ["cancel", "W-1", "--revert-merged"])
        assert result.exit_code == 0


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
