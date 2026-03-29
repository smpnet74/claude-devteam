"""Tests for v2 job CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from devteam.orchestrator.runtime_state import RuntimeStateStore

runner = CliRunner()


@pytest.fixture
def runtime_store(tmp_path: Path):
    s = RuntimeStateStore(str(tmp_path / "runtime.sqlite"))
    yield s
    s.close()


def _make_app():
    """Create a typer app with v2 job commands registered."""
    import typer

    from devteam.cli.commands.job_cmd_v2 import register_job_commands_v2

    app = typer.Typer()
    register_job_commands_v2(app)
    return app


class TestStatusCommand:
    def test_no_active_jobs(self, runtime_store: RuntimeStateStore) -> None:
        app = _make_app()
        with patch("devteam.cli.commands.job_cmd_v2._get_store", return_value=runtime_store):
            result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "No active jobs" in result.output

    def test_active_job_listed(self, runtime_store: RuntimeStateStore) -> None:
        runtime_store.register_job(workflow_id="uuid-1", project_name="proj", repo_root="/tmp")
        app = _make_app()
        with patch("devteam.cli.commands.job_cmd_v2._get_store", return_value=runtime_store):
            result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "W-1" in result.output

    def test_specific_job_status(self, runtime_store: RuntimeStateStore) -> None:
        runtime_store.register_job(workflow_id="uuid-1", project_name="myproj", repo_root="/tmp")
        runtime_store.register_task(
            alias="T-1", workflow_id="c1", job_alias="W-1", assigned_to="backend_engineer"
        )
        app = _make_app()
        with patch("devteam.cli.commands.job_cmd_v2._get_store", return_value=runtime_store):
            result = runner.invoke(app, ["status", "W-1"])
        assert result.exit_code == 0
        assert "W-1" in result.output
        assert "T-1" in result.output
        assert "backend_engineer" in result.output

    def test_job_not_found(self, runtime_store: RuntimeStateStore) -> None:
        app = _make_app()
        with patch("devteam.cli.commands.job_cmd_v2._get_store", return_value=runtime_store):
            result = runner.invoke(app, ["status", "W-99"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_pending_questions(self, runtime_store: RuntimeStateStore) -> None:
        runtime_store.register_job(workflow_id="uuid-1", project_name="proj", repo_root="/tmp")
        runtime_store.register_task(
            alias="T-1", workflow_id="c1", job_alias="W-1", assigned_to="be"
        )
        runtime_store.register_question(
            internal_id="Q-T-1-1",
            child_workflow_id="child-uuid",
            task_alias="T-1",
            text="Redis or JWT?",
            tier=2,
        )
        app = _make_app()
        with patch("devteam.cli.commands.job_cmd_v2._get_store", return_value=runtime_store):
            result = runner.invoke(app, ["status", "--questions"])
        assert result.exit_code == 0
        assert "Q-1" in result.output
        assert "Redis or JWT?" in result.output


class TestStartCommand:
    def test_no_args_fails(self) -> None:
        app = _make_app()
        result = runner.invoke(app, ["start"])
        assert result.exit_code == 1
        assert "Provide" in result.output

    def test_start_with_prompt(self) -> None:
        app = _make_app()
        mock_handle = MagicMock()
        mock_handle.workflow_id = "wf-123"

        with patch(
            "devteam.cli.commands.job_cmd_v2.asyncio.run",
            return_value=(mock_handle, "W-1"),
        ):
            result = runner.invoke(app, ["start", "--prompt", "Fix the bug"])
        assert result.exit_code == 0
        assert "W-1" in result.output


class TestAnswerCommand:
    def test_question_not_found(self, runtime_store: RuntimeStateStore) -> None:
        app = _make_app()
        with patch("devteam.cli.commands.job_cmd_v2._get_store", return_value=runtime_store):
            result = runner.invoke(app, ["answer", "Q-99", "Use JWT"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_answer_resolved_question(self, runtime_store: RuntimeStateStore) -> None:
        runtime_store.register_job(workflow_id="uuid-1", project_name="proj", repo_root="/tmp")
        runtime_store.register_task(
            alias="T-1", workflow_id="c1", job_alias="W-1", assigned_to="be"
        )
        runtime_store.register_question(
            internal_id="Q-T-1-1",
            child_workflow_id="child-uuid",
            task_alias="T-1",
            text="Redis or JWT?",
            tier=2,
        )
        runtime_store.resolve_question("Q-1")
        app = _make_app()
        with patch("devteam.cli.commands.job_cmd_v2._get_store", return_value=runtime_store):
            result = runner.invoke(app, ["answer", "Q-1", "JWT"])
        assert result.exit_code == 0
        assert "already resolved" in result.output
