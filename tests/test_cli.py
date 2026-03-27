"""Tests for the CLI interface."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from devteam.cli.main import app
import devteam.cli.commands.job_cmd as job_cmd_mod

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_job_store() -> None:
    """Reset the module-level singleton store before each test."""
    job_cmd_mod._store = None


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
        assert "already" in result.output.lower()


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

    def test_daemon_start_without_init(self, tmp_path: Path) -> None:
        devteam_home = tmp_path / "nonexistent_devteam"
        with patch("devteam.cli.commands.daemon_cmd.get_devteam_home", return_value=devteam_home):
            result = runner.invoke(app, ["daemon", "start", "--foreground"])
        assert result.exit_code == 1
        assert "not initialized" in result.output.lower()


class TestProjectCommands:
    def test_project_add_copies_agents(self, tmp_path: Path) -> None:
        # Set up a fake devteam home with agents
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
        # Don't create agents dir — simulates not having run `devteam init`
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


class TestJobCommands:
    def test_start_help(self) -> None:
        result = runner.invoke(app, ["start", "--help"])
        assert result.exit_code == 0
        assert "spec" in result.output.lower() or "plan" in result.output.lower()

    def test_start_creates_job_with_spec(self) -> None:
        result = runner.invoke(app, ["start", "--spec", "Build an API"])
        assert result.exit_code == 0
        assert "W-1" in result.output
        assert "created" in result.output

    def test_start_with_plan_only(self) -> None:
        result = runner.invoke(app, ["start", "--plan", "Step 1: build it"])
        assert result.exit_code == 0
        assert "W-1" in result.output

    def test_start_with_prompt(self) -> None:
        result = runner.invoke(app, ["start", "--prompt", "Fix the bug"])
        assert result.exit_code == 0
        assert "W-1" in result.output

    def test_start_with_issue(self) -> None:
        result = runner.invoke(
            app, ["start", "--issue", "https://github.com/org/repo/issues/42"]
        )
        assert result.exit_code == 0
        assert "W-1" in result.output

    def test_start_no_args_exits_1(self) -> None:
        result = runner.invoke(app, ["start"])
        assert result.exit_code == 1

    def test_start_sequential_ids(self) -> None:
        runner.invoke(app, ["start", "--prompt", "first"])
        result = runner.invoke(app, ["start", "--prompt", "second"])
        assert result.exit_code == 0
        assert "W-2" in result.output

    def test_status_no_jobs(self) -> None:
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "no active jobs" in result.output.lower()

    def test_status_with_job_id(self) -> None:
        runner.invoke(app, ["start", "--prompt", "test"])
        result = runner.invoke(app, ["status", "W-1"])
        assert result.exit_code == 0
        assert "W-1" in result.output

    def test_status_nonexistent_job(self) -> None:
        result = runner.invoke(app, ["status", "W-99"])
        assert result.exit_code == 1

    def test_status_questions_flag(self) -> None:
        result = runner.invoke(app, ["status", "--questions"])
        assert result.exit_code == 0
        assert "no pending questions" in result.output.lower()

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

    def test_cancel_existing_job(self) -> None:
        runner.invoke(app, ["start", "--prompt", "test"])
        result = runner.invoke(app, ["cancel", "W-1"])
        assert result.exit_code == 0
        assert "canceled" in result.output.lower()

    def test_cancel_nonexistent_job(self) -> None:
        result = runner.invoke(app, ["cancel", "W-99"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_cancel_revert_merged(self) -> None:
        runner.invoke(app, ["start", "--prompt", "test"])
        result = runner.invoke(app, ["cancel", "W-1", "--revert-merged"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()


class TestCommentCommand:
    def test_comment_on_task(self) -> None:
        runner.invoke(app, ["start", "--prompt", "test"])
        result = runner.invoke(app, ["comment", "W-1/T-3", "Use PostgreSQL"])
        assert result.exit_code == 0
        assert "comment added" in result.output.lower()

    def test_comment_shorthand(self) -> None:
        runner.invoke(app, ["start", "--prompt", "test"])
        result = runner.invoke(app, ["comment", "T-3", "feedback"])
        assert result.exit_code == 0

    def test_comment_nonexistent_target(self) -> None:
        result = runner.invoke(app, ["comment", "W-99/T-1", "feedback"])
        assert result.exit_code == 1

    def test_comment_help(self) -> None:
        result = runner.invoke(app, ["comment", "--help"])
        assert result.exit_code == 0


class TestAnswerCommand:
    def test_answer_nonexistent_question(self) -> None:
        result = runner.invoke(app, ["answer", "Q-99", "Use Redis"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_answer_existing_question(self) -> None:
        from devteam.orchestrator.cli_bridge import QuestionTracker
        from devteam.orchestrator.schemas import QuestionRecord, QuestionType

        # Manually seed a question in the store
        store = job_cmd_mod._get_store()
        q = QuestionTracker(
            id="Q-1",
            task_id="T-2",
            job_id="W-1",
            record=QuestionRecord(
                question="Redis or Memcached?",
                question_type=QuestionType.TECHNICAL,
            ),
        )
        store.save_question(q)

        result = runner.invoke(app, ["answer", "Q-1", "Use Redis"])
        assert result.exit_code == 0
        assert "answer recorded" in result.output.lower()

    def test_answer_help(self) -> None:
        result = runner.invoke(app, ["answer", "--help"])
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
