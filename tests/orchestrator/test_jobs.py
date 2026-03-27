"""Tests for job lifecycle management."""

import pytest
from unittest.mock import MagicMock

from devteam.models.entities import JobStatus
from devteam.orchestrator.jobs import (
    Job,
    create_job,
    determine_work_type_from_route,
    execute_job,
    needs_post_pr_review,
    transition_job,
)
from devteam.orchestrator.routing import IntakeContext
from devteam.orchestrator.schemas import (
    RoutePath,
    WorkType,
)


# ---------------------------------------------------------------------------
# create_job
# ---------------------------------------------------------------------------


class TestCreateJob:
    def test_creates_with_defaults(self) -> None:
        intake = IntakeContext(spec="spec", plan="plan")
        job = create_job("W-1", "My App", intake)
        assert job.id == "W-1"
        assert job.status == JobStatus.CREATED
        assert job.completed_at is None

    def test_progress_empty(self) -> None:
        job = create_job("W-1", "Test", IntakeContext())
        assert job.progress == (0, 0)

    def test_intake_attached(self) -> None:
        intake = IntakeContext(spec="build it", plan="step 1")
        job = create_job("W-1", "My App", intake)
        assert job.intake is not None
        assert job.intake.spec == "build it"

    def test_id_format(self) -> None:
        job = create_job("W-42", "Test", IntakeContext())
        assert job.id == "W-42"


# ---------------------------------------------------------------------------
# transition_job
# ---------------------------------------------------------------------------


class TestTransitionJob:
    def test_valid_transition(self) -> None:
        job = Job(id="W-1", title="Test")
        transition_job(job, JobStatus.PLANNING)
        assert job.status == JobStatus.PLANNING

    def test_invalid_transition_raises(self) -> None:
        job = Job(id="W-1", title="Test")
        with pytest.raises(ValueError, match="Invalid transition"):
            transition_job(job, JobStatus.COMPLETED)

    def test_full_lifecycle(self) -> None:
        job = Job(id="W-1", title="Test")
        transition_job(job, JobStatus.PLANNING)
        transition_job(job, JobStatus.DECOMPOSING)
        transition_job(job, JobStatus.EXECUTING)
        transition_job(job, JobStatus.REVIEWING)
        transition_job(job, JobStatus.COMPLETED)
        assert job.status == JobStatus.COMPLETED
        assert job.completed_at is not None

    def test_cancel_from_any_active_state(self) -> None:
        for status in [
            JobStatus.CREATED,
            JobStatus.PLANNING,
            JobStatus.DECOMPOSING,
            JobStatus.EXECUTING,
            JobStatus.REVIEWING,
        ]:
            job = Job(id="W-1", title="Test", status=status)
            transition_job(job, JobStatus.CANCELED)
            assert job.status == JobStatus.CANCELED

    def test_cannot_transition_from_completed(self) -> None:
        job = Job(id="W-1", title="Test", status=JobStatus.COMPLETED)
        with pytest.raises(ValueError):
            transition_job(job, JobStatus.PLANNING)

    def test_cannot_transition_from_failed(self) -> None:
        job = Job(id="W-1", title="Test", status=JobStatus.FAILED)
        with pytest.raises(ValueError):
            transition_job(job, JobStatus.PLANNING)

    def test_cannot_transition_from_canceled(self) -> None:
        job = Job(id="W-1", title="Test", status=JobStatus.CANCELED)
        with pytest.raises(ValueError):
            transition_job(job, JobStatus.PLANNING)

    def test_rate_limit_pause_and_resume(self) -> None:
        job = Job(id="W-1", title="Test", status=JobStatus.EXECUTING)
        transition_job(job, JobStatus.PAUSED_RATE_LIMIT)
        transition_job(job, JobStatus.EXECUTING)
        assert job.status == JobStatus.EXECUTING

    def test_small_fix_skips_decomposition(self) -> None:
        """Small fix can go directly from planning to executing."""
        job = Job(id="W-1", title="Test", status=JobStatus.PLANNING)
        transition_job(job, JobStatus.EXECUTING)
        assert job.status == JobStatus.EXECUTING


# ---------------------------------------------------------------------------
# Route/work-type helpers
# ---------------------------------------------------------------------------


class TestRouteWorkTypeMapping:
    def test_full_project_is_code(self) -> None:
        assert determine_work_type_from_route(RoutePath.FULL_PROJECT) == WorkType.CODE

    def test_research_is_research(self) -> None:
        assert determine_work_type_from_route(RoutePath.RESEARCH) == WorkType.RESEARCH

    def test_small_fix_is_code(self) -> None:
        assert determine_work_type_from_route(RoutePath.SMALL_FIX) == WorkType.CODE

    def test_oss_is_code(self) -> None:
        assert determine_work_type_from_route(RoutePath.OSS_CONTRIBUTION) == WorkType.CODE


class TestNeedsPostPRReview:
    def test_full_project_needs_review(self) -> None:
        assert needs_post_pr_review(RoutePath.FULL_PROJECT)

    def test_research_no_review(self) -> None:
        assert not needs_post_pr_review(RoutePath.RESEARCH)

    def test_small_fix_needs_review(self) -> None:
        assert needs_post_pr_review(RoutePath.SMALL_FIX)

    def test_oss_needs_review(self) -> None:
        assert needs_post_pr_review(RoutePath.OSS_CONTRIBUTION)


# ---------------------------------------------------------------------------
# execute_job
# ---------------------------------------------------------------------------


class TestExecuteJob:
    def test_full_project_lifecycle(self) -> None:
        """Full project: route -> decompose -> execute DAG -> review."""
        invoker = MagicMock()

        # Route returns full_project (fast path for spec+plan -- no CEO call)
        # CA decomposition + post-PR reviews (QA, Security, Tech Writer)
        invoker.invoke.side_effect = [
            # decompose call to chief_architect
            {
                "tasks": [
                    {
                        "id": "T-1",
                        "description": "Build API",
                        "assigned_to": "backend_engineer",
                        "team": "a",
                        "depends_on": [],
                        "pr_group": "feat/api",
                        "work_type": "code",
                    },
                ],
                "peer_assignments": {"T-1": "frontend_engineer"},
                "parallel_groups": [["T-1"]],
            },
            # Post-PR reviews (QA, Security, Tech Writer)
            {"verdict": "approved", "comments": [], "summary": "ok"},
            {"verdict": "approved", "comments": [], "summary": "ok"},
            {"verdict": "approved", "comments": [], "summary": "ok"},
        ]

        intake = IntakeContext(spec="Build an API", plan="Step 1: schema")
        job = create_job("W-1", "My App", intake)

        def launch(task):  # type: ignore[no-untyped-def]
            return task.id

        def wait(handle):  # type: ignore[no-untyped-def]
            return (True, {"status": "completed"})

        result = execute_job(job, invoker, task_launcher=launch, task_checker=wait)
        assert result.status == JobStatus.COMPLETED

    def test_research_path_no_decomposition(self) -> None:
        """Research path skips decomposition and post-PR review."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "path": "research",
            "reasoning": "User wants analysis",
        }
        intake = IntakeContext(prompt="Research auth strategies")
        job = create_job("W-1", "Research", intake)

        result = execute_job(job, invoker)
        assert result.status == JobStatus.COMPLETED
        assert result.decomposition is None

    def test_job_without_intake_raises(self) -> None:
        job = Job(id="W-1", title="Test", intake=None)
        with pytest.raises(ValueError, match="no intake"):
            execute_job(job, MagicMock())

    def test_routing_failure_transitions_to_failed(self) -> None:
        """If routing invocation fails, job transitions to FAILED."""
        invoker = MagicMock()
        invoker.invoke.side_effect = ConnectionError("timeout")

        intake = IntakeContext(prompt="do something")
        job = create_job("W-1", "Test", intake)

        result = execute_job(job, invoker)
        assert result.status == JobStatus.FAILED
        assert result.error is not None

    def test_cancellation_before_routing(self) -> None:
        """If cancelled flag is set before execution, job is canceled."""
        invoker = MagicMock()
        intake = IntakeContext(spec="spec", plan="plan")
        job = create_job("W-1", "Test", intake)
        job.cancelled = True

        result = execute_job(job, invoker)
        assert result.status == JobStatus.CANCELED
        # Invoker should not have been called
        invoker.invoke.assert_not_called()

    def test_cancellation_after_routing(self) -> None:
        """Cancellation is checked after each major step."""
        import devteam.orchestrator.jobs as jobs_mod

        invoker = MagicMock()
        intake = IntakeContext(prompt="do something")
        job = create_job("W-1", "Test", intake)

        # Monkeypatch route_intake to set cancelled flag mid-workflow
        original_fn = jobs_mod.route_intake

        def fake_route(ctx, inv):  # type: ignore[no-untyped-def]
            from devteam.orchestrator.schemas import RoutingResult

            job.cancelled = True
            return RoutingResult(
                path=RoutePath.FULL_PROJECT,
                reasoning="test",
            )

        jobs_mod.route_intake = fake_route  # type: ignore[assignment]
        try:
            result = execute_job(job, invoker)
        finally:
            jobs_mod.route_intake = original_fn  # type: ignore[assignment]

        assert result.status == JobStatus.CANCELED

    def test_dag_failure_transitions_to_failed(self) -> None:
        """If some DAG tasks fail, the job transitions to FAILED."""
        invoker = MagicMock()

        invoker.invoke.side_effect = [
            # CA decomposition
            {
                "tasks": [
                    {
                        "id": "T-1",
                        "description": "Build API",
                        "assigned_to": "backend_engineer",
                        "team": "a",
                        "depends_on": [],
                        "pr_group": "feat/api",
                        "work_type": "code",
                    },
                ],
                "peer_assignments": {"T-1": "frontend_engineer"},
                "parallel_groups": [["T-1"]],
            },
        ]

        intake = IntakeContext(spec="Build an API", plan="Step 1")
        job = create_job("W-1", "My App", intake)

        def launch(task):  # type: ignore[no-untyped-def]
            return task.id

        def wait(handle):  # type: ignore[no-untyped-def]
            return (True, Exception("task exploded"))

        result = execute_job(job, invoker, task_launcher=launch, task_checker=wait)
        assert result.status == JobStatus.FAILED


# ---------------------------------------------------------------------------
# Job.add_comment
# ---------------------------------------------------------------------------


class TestJobComments:
    def test_add_comment(self) -> None:
        job = create_job("W-1", "Test", IntakeContext())
        job.add_comment("Use PostgreSQL instead")
        assert len(job.comments) == 1
        assert "PostgreSQL" in job.comments[0]

    def test_multiple_comments(self) -> None:
        job = create_job("W-1", "Test", IntakeContext())
        job.add_comment("first")
        job.add_comment("second")
        assert len(job.comments) == 2
