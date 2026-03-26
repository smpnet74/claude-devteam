"""Integration tests — verify the full stack wires together."""

import os
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from devteam.cli.main import app
from devteam.config.settings import load_global_config
from devteam.daemon.database import init_database
from devteam.daemon.process import (
    acquire_pid_lock,
    get_daemon_state,
    release_pid_lock,
)
from devteam.models.entities import Job, JobStatus, Task, TaskStatus
from devteam.models.state import validate_job_transition, validate_task_transition

runner = CliRunner()


class TestFullInitFlow:
    def test_init_then_daemon_status(self, tmp_path: Path) -> None:
        """init creates the structure, daemon status reports not running."""
        devteam_home = tmp_path / ".devteam"

        with patch("devteam.cli.commands.init_cmd.get_devteam_home", return_value=devteam_home):
            result = runner.invoke(app, ["init"])
        assert result.exit_code == 0

        # Config file is loadable
        config = load_global_config(devteam_home / "config.toml")
        assert config.daemon.port == 7432

        # Database can initialize
        db_config = init_database(devteam_home)
        assert db_config.db_path.parent.exists()

        # Daemon status works
        with patch("devteam.cli.commands.daemon_cmd.get_devteam_home", return_value=devteam_home):
            result = runner.invoke(app, ["daemon", "status"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()


class TestEntityLifecycleFlow:
    def test_job_through_lifecycle(self) -> None:
        """A Job can transition through the full happy path."""
        job = Job(job_id="W-1", title="Integration Test")
        assert job.status == JobStatus.CREATED

        # Simulate lifecycle transitions
        transitions = [
            JobStatus.PLANNING,
            JobStatus.DECOMPOSING,
            JobStatus.EXECUTING,
            JobStatus.REVIEWING,
            JobStatus.COMPLETED,
        ]
        current = job.status
        for next_status in transitions:
            validate_job_transition(current, next_status)
            current = next_status

    def test_task_with_question_flow(self) -> None:
        """A Task can pause for a question and resume."""
        task = Task(
            task_id="T-1",
            job_id="W-1",
            description="Build auth",
            assigned_to="backend",
            app="api",
        )

        # queued -> assigned -> executing -> waiting_on_question -> executing ->
        # waiting_on_review -> approved -> completed
        transitions = [
            TaskStatus.ASSIGNED,
            TaskStatus.EXECUTING,
            TaskStatus.WAITING_ON_QUESTION,
            TaskStatus.EXECUTING,
            TaskStatus.WAITING_ON_REVIEW,
            TaskStatus.APPROVED,
            TaskStatus.COMPLETED,
        ]
        current = task.status
        for next_status in transitions:
            validate_task_transition(current, next_status)
            current = next_status

    def test_task_revision_loop(self) -> None:
        """A Task can go through multiple revision cycles."""
        transitions = [
            (TaskStatus.QUEUED, TaskStatus.ASSIGNED),
            (TaskStatus.ASSIGNED, TaskStatus.EXECUTING),
            (TaskStatus.EXECUTING, TaskStatus.WAITING_ON_REVIEW),
            (TaskStatus.WAITING_ON_REVIEW, TaskStatus.REVISION_REQUESTED),
            (TaskStatus.REVISION_REQUESTED, TaskStatus.EXECUTING),
            (TaskStatus.EXECUTING, TaskStatus.WAITING_ON_REVIEW),
            (TaskStatus.WAITING_ON_REVIEW, TaskStatus.APPROVED),
            (TaskStatus.APPROVED, TaskStatus.COMPLETED),
        ]
        for from_state, to_state in transitions:
            validate_task_transition(from_state, to_state)


class TestPIDLockIntegration:
    def test_acquire_release_cycle(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        port_path = tmp_devteam_home / "daemon.port"

        # Initially not running
        state = get_daemon_state(pid_path, port_path)
        assert state.running is False

        # Acquire lock
        acquire_pid_lock(pid_path, os.getpid())
        state = get_daemon_state(pid_path, port_path)
        assert state.running is True

        # Release lock
        release_pid_lock(pid_path)
        state = get_daemon_state(pid_path, port_path)
        assert state.running is False
