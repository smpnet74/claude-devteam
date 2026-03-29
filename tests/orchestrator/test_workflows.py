"""Tests for DBOS workflow definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dbos import DBOS

from devteam.orchestrator.runtime_state import RuntimeStateStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime_store(tmp_path: Path):
    s = RuntimeStateStore(str(tmp_path / "runtime.sqlite"))
    yield s
    s.close()


def _make_task_dict(**overrides: Any) -> dict[str, Any]:
    """Build a valid TaskDecomposition dict."""
    base = {
        "id": "T-1",
        "description": "Build login API",
        "assigned_to": "backend_engineer",
        "team": "a",
        "depends_on": [],
        "pr_group": "auth",
        "work_type": "code",
    }
    base.update(overrides)
    return base


def _impl_result(status: str = "completed", question: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "status": status,
        "files_changed": [],
        "tests_added": [],
        "summary": "Implemented the feature",
        "confidence": "high",
    }
    if question:
        d["question"] = question
    return d


def _review_result(verdict: str = "approved") -> dict[str, Any]:
    d: dict[str, Any] = {"verdict": verdict, "summary": "Looks good"}
    if verdict in ("needs_revision", "approved_with_comments", "blocked"):
        d["comments"] = [
            {"file": "main.py", "line": 1, "severity": "warning", "comment": "fix this"}
        ]
    return d


# ---------------------------------------------------------------------------
# TestExecuteTask
# ---------------------------------------------------------------------------


class TestExecuteTask:
    """Tests for execute_task workflow."""

    @pytest.mark.asyncio
    async def test_happy_path(self, dbos_launch: Any, runtime_store: RuntimeStateStore) -> None:
        """Engineer completes → peer approves → EM approves → PR created."""
        from devteam.orchestrator.workflows import execute_task

        # Register parent job and task so FK constraints pass
        runtime_store.register_job(workflow_id="parent-uuid", project_name="proj", repo_root="/tmp")
        runtime_store.register_task(
            alias="T-1", workflow_id="task-uuid", job_alias="W-1", assigned_to="backend_engineer"
        )

        mock_wt = MagicMock()
        mock_wt.path = Path("/tmp/worktrees/T-1")

        mock_pr = MagicMock()
        mock_pr.number = 42
        mock_pr.url = "https://github.com/owner/repo/pull/42"

        # invoke_agent_step returns: impl(completed), peer(approved), em(approved)
        invoke_responses = [
            _impl_result("completed"),
            _review_result("approved"),
            _review_result("approved"),
        ]

        with (
            patch(
                "devteam.orchestrator.workflows.create_worktree_step",
                new_callable=AsyncMock,
                return_value=mock_wt,
            ),
            patch(
                "devteam.orchestrator.workflows.invoke_agent_step",
                new_callable=AsyncMock,
                side_effect=invoke_responses,
            ),
            patch(
                "devteam.orchestrator.workflows.create_pr_step",
                new_callable=AsyncMock,
                return_value=mock_pr,
            ),
            patch("devteam.orchestrator.bootstrap.get_runtime_store", return_value=runtime_store),
        ):
            result = await execute_task(
                task=_make_task_dict(),
                job_alias="W-1",
                project_name="proj",
                repo_root="/tmp/repo",
                peer_reviewer="frontend_engineer",
            )

        assert result["status"] == "completed"
        assert result["pr_number"] == 42
        assert result["revisions"] == 1

    @pytest.mark.asyncio
    async def test_happy_path_no_peer_reviewer(
        self, dbos_launch: Any, runtime_store: RuntimeStateStore
    ) -> None:
        """Without peer reviewer, goes straight to EM review."""
        from devteam.orchestrator.workflows import execute_task

        runtime_store.register_job(workflow_id="parent-uuid", project_name="proj", repo_root="/tmp")
        runtime_store.register_task(
            alias="T-1", workflow_id="task-uuid", job_alias="W-1", assigned_to="backend_engineer"
        )

        mock_wt = MagicMock()
        mock_wt.path = Path("/tmp/worktrees/T-1")

        mock_pr = MagicMock()
        mock_pr.number = 10
        mock_pr.url = "https://github.com/owner/repo/pull/10"

        # No peer review — just impl(completed) + em(approved)
        invoke_responses = [
            _impl_result("completed"),
            _review_result("approved"),
        ]

        with (
            patch(
                "devteam.orchestrator.workflows.create_worktree_step",
                new_callable=AsyncMock,
                return_value=mock_wt,
            ),
            patch(
                "devteam.orchestrator.workflows.invoke_agent_step",
                new_callable=AsyncMock,
                side_effect=invoke_responses,
            ),
            patch(
                "devteam.orchestrator.workflows.create_pr_step",
                new_callable=AsyncMock,
                return_value=mock_pr,
            ),
            patch("devteam.orchestrator.bootstrap.get_runtime_store", return_value=runtime_store),
        ):
            result = await execute_task(
                task=_make_task_dict(),
                job_alias="W-1",
                project_name="proj",
                repo_root="/tmp/repo",
                peer_reviewer=None,
            )

        assert result["status"] == "completed"
        assert result["revisions"] == 1

    @pytest.mark.asyncio
    async def test_question_flow(self, dbos_launch: Any, runtime_store: RuntimeStateStore) -> None:
        """Engineer asks question → answer received → completes on retry."""
        from devteam.orchestrator.workflows import execute_task

        runtime_store.register_job(workflow_id="parent-uuid", project_name="proj", repo_root="/tmp")
        runtime_store.register_task(
            alias="T-1", workflow_id="task-uuid", job_alias="W-1", assigned_to="backend_engineer"
        )

        mock_wt = MagicMock()
        mock_wt.path = Path("/tmp/worktrees/T-1")

        mock_pr = MagicMock()
        mock_pr.number = 5
        mock_pr.url = "https://github.com/owner/repo/pull/5"

        # First call: needs_clarification; second: completed; then EM review
        invoke_responses = [
            _impl_result("needs_clarification", question="Redis or JWT?"),
            _impl_result("completed"),
            _review_result("approved"),
        ]

        with (
            patch(
                "devteam.orchestrator.workflows.create_worktree_step",
                new_callable=AsyncMock,
                return_value=mock_wt,
            ),
            patch(
                "devteam.orchestrator.workflows.invoke_agent_step",
                new_callable=AsyncMock,
                side_effect=invoke_responses,
            ),
            patch(
                "devteam.orchestrator.workflows.create_pr_step",
                new_callable=AsyncMock,
                return_value=mock_pr,
            ),
            patch("devteam.orchestrator.bootstrap.get_runtime_store", return_value=runtime_store),
            patch.object(DBOS, "set_event"),
            patch.object(DBOS, "recv", return_value="Use JWT"),
        ):
            result = await execute_task(
                task=_make_task_dict(),
                job_alias="W-1",
                project_name="proj",
                repo_root="/tmp/repo",
            )

        assert result["status"] == "completed"
        assert result["revisions"] == 2
        # Question should be registered and resolved
        questions = runtime_store.get_pending_questions("W-1")
        assert len(questions) == 0  # resolved

    @pytest.mark.asyncio
    async def test_revision_loop(self, dbos_launch: Any, runtime_store: RuntimeStateStore) -> None:
        """Peer returns needs_revision → engineer revises → peer approves."""
        from devteam.orchestrator.workflows import execute_task

        runtime_store.register_job(workflow_id="parent-uuid", project_name="proj", repo_root="/tmp")
        runtime_store.register_task(
            alias="T-1", workflow_id="task-uuid", job_alias="W-1", assigned_to="backend_engineer"
        )

        mock_wt = MagicMock()
        mock_wt.path = Path("/tmp/worktrees/T-1")

        mock_pr = MagicMock()
        mock_pr.number = 7
        mock_pr.url = "https://github.com/owner/repo/pull/7"

        # Round 1: impl ok, peer rejects
        # Round 2: impl ok, peer approves, EM approves
        invoke_responses = [
            _impl_result("completed"),
            _review_result("needs_revision"),
            _impl_result("completed"),
            _review_result("approved"),
            _review_result("approved"),
        ]

        with (
            patch(
                "devteam.orchestrator.workflows.create_worktree_step",
                new_callable=AsyncMock,
                return_value=mock_wt,
            ),
            patch(
                "devteam.orchestrator.workflows.invoke_agent_step",
                new_callable=AsyncMock,
                side_effect=invoke_responses,
            ),
            patch(
                "devteam.orchestrator.workflows.create_pr_step",
                new_callable=AsyncMock,
                return_value=mock_pr,
            ),
            patch("devteam.orchestrator.bootstrap.get_runtime_store", return_value=runtime_store),
        ):
            result = await execute_task(
                task=_make_task_dict(),
                job_alias="W-1",
                project_name="proj",
                repo_root="/tmp/repo",
                peer_reviewer="frontend_engineer",
            )

        assert result["status"] == "completed"
        assert result["revisions"] == 2

    @pytest.mark.asyncio
    async def test_max_revisions_exceeded(
        self, dbos_launch: Any, runtime_store: RuntimeStateStore
    ) -> None:
        """3 revision failures → task fails."""
        from devteam.orchestrator.workflows import execute_task

        runtime_store.register_job(workflow_id="parent-uuid", project_name="proj", repo_root="/tmp")
        runtime_store.register_task(
            alias="T-1", workflow_id="task-uuid", job_alias="W-1", assigned_to="backend_engineer"
        )

        mock_wt = MagicMock()
        mock_wt.path = Path("/tmp/worktrees/T-1")

        # Every round: impl completes but peer rejects (3 times)
        invoke_responses = [
            _impl_result("completed"),
            _review_result("needs_revision"),
            _impl_result("completed"),
            _review_result("needs_revision"),
            _impl_result("completed"),
            _review_result("needs_revision"),
        ]

        with (
            patch(
                "devteam.orchestrator.workflows.create_worktree_step",
                new_callable=AsyncMock,
                return_value=mock_wt,
            ),
            patch(
                "devteam.orchestrator.workflows.invoke_agent_step",
                new_callable=AsyncMock,
                side_effect=invoke_responses,
            ),
            patch("devteam.orchestrator.bootstrap.get_runtime_store", return_value=runtime_store),
        ):
            result = await execute_task(
                task=_make_task_dict(),
                job_alias="W-1",
                project_name="proj",
                repo_root="/tmp/repo",
                peer_reviewer="frontend_engineer",
            )

        assert result["status"] == "max_revisions_exceeded"
        assert result["revisions"] == 3
