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


# ---------------------------------------------------------------------------
# TestExecuteJob
# ---------------------------------------------------------------------------


class TestExecuteJob:
    """Tests for execute_job parent workflow."""

    @pytest.mark.asyncio
    async def test_research_path(self, dbos_launch: Any, runtime_store: RuntimeStateStore) -> None:
        """Research route: single agent call, return result."""
        from devteam.orchestrator.workflows import execute_job

        runtime_store.register_job(workflow_id="job-uuid", project_name="proj", repo_root="/tmp")

        research_result = {"findings": "CI best practices report"}

        with (
            patch(
                "devteam.orchestrator.workflows.route_intake_step",
                new_callable=AsyncMock,
                return_value=MagicMock(path=MagicMock(value="research"), target_team=None),
            ) as mock_route,
            patch(
                "devteam.orchestrator.workflows.invoke_agent_step",
                new_callable=AsyncMock,
                return_value=research_result,
            ),
            patch("devteam.orchestrator.bootstrap.get_runtime_store", return_value=runtime_store),
        ):
            # Make route_intake_step return a RoutePath.RESEARCH
            from devteam.orchestrator.schemas import RoutePath, RoutingResult

            mock_route.return_value = RoutingResult(
                path=RoutePath.RESEARCH, reasoning="Research request"
            )

            result = await execute_job(
                spec="Research CI practices",
                plan="",
                project_name="proj",
                repo_root="/tmp/repo",
            )

        assert result["status"] == "completed"
        assert result["route"] == "research"

    @pytest.mark.asyncio
    async def test_small_fix_path(self, dbos_launch: Any, runtime_store: RuntimeStateStore) -> None:
        """Small fix route: single task, no decomposition."""
        from devteam.orchestrator.schemas import RoutePath, RoutingResult
        from devteam.orchestrator.workflows import execute_job

        runtime_store.register_job(workflow_id="job-uuid", project_name="proj", repo_root="/tmp")

        task_result = {
            "status": "completed",
            "task_id": "T-1",
            "revisions": 1,
            "pr_number": 1,
            "pr_url": "url",
        }

        with (
            patch(
                "devteam.orchestrator.workflows.route_intake_step",
                new_callable=AsyncMock,
                return_value=RoutingResult(
                    path=RoutePath.SMALL_FIX, reasoning="Small fix", target_team="a"
                ),
            ),
            patch(
                "devteam.orchestrator.workflows.execute_task",
                new_callable=AsyncMock,
                return_value=task_result,
            ),
            patch("devteam.orchestrator.bootstrap.get_runtime_store", return_value=runtime_store),
        ):
            result = await execute_job(
                spec="Fix typo in readme",
                plan="",
                project_name="proj",
                repo_root="/tmp/repo",
            )

        assert result["status"] == "completed"
        assert result["route"] == "small_fix"
        assert len(result["tasks"]) == 1

    @pytest.mark.asyncio
    async def test_full_project_path(
        self, dbos_launch: Any, runtime_store: RuntimeStateStore
    ) -> None:
        """Full project: decompose → DAG → post-PR review."""
        from devteam.orchestrator.schemas import RoutePath, RoutingResult
        from devteam.orchestrator.workflows import execute_job

        runtime_store.register_job(workflow_id="job-uuid", project_name="proj", repo_root="/tmp")

        task_result = {
            "status": "completed",
            "task_id": "T-1",
            "revisions": 1,
            "pr_number": 42,
            "pr_url": "url",
        }

        decomp_result = MagicMock()
        decomp_result.tasks = [
            MagicMock(
                id="T-1",
                assigned_to="backend_engineer",
                team="a",
                depends_on=[],
                pr_group="auth",
                work_type="code",
            )
        ]
        decomp_result.tasks[0].model_dump.return_value = _make_task_dict()
        decomp_result.peer_assignments = {"T-1": "frontend_engineer"}

        with (
            patch(
                "devteam.orchestrator.workflows.route_intake_step",
                new_callable=AsyncMock,
                return_value=RoutingResult(
                    path=RoutePath.FULL_PROJECT,
                    reasoning="Spec and plan provided",
                ),
            ),
            patch(
                "devteam.orchestrator.workflows.decompose_step",
                new_callable=AsyncMock,
                return_value=decomp_result,
            ),
            patch(
                "devteam.orchestrator.workflows.build_dag",
            ) as mock_build_dag,
            patch(
                "devteam.orchestrator.workflows.execute_task",
                new_callable=AsyncMock,
                return_value=task_result,
            ),
            patch(
                "devteam.orchestrator.workflows.post_pr_review_step",
                new_callable=AsyncMock,
            ),
            patch("devteam.orchestrator.bootstrap.get_runtime_store", return_value=runtime_store),
        ):
            # Set up DAG mock
            from devteam.orchestrator.dag import DAGState, TaskNode

            dag = DAGState()
            dag.nodes["T-1"] = TaskNode(task=decomp_result.tasks[0])
            dag.dependency_graph["T-1"] = []
            mock_build_dag.return_value = dag

            result = await execute_job(
                spec="Build auth system",
                plan="Step 1: API",
                project_name="proj",
                repo_root="/tmp/repo",
            )

        assert result["status"] == "completed"
        assert result["route"] == "full_project"
        assert len(result["tasks"]) == 1
