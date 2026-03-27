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
        with pytest.raises(ValueError, match="Invalid.*transition"):
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

    def test_non_research_without_launchers_raises(self) -> None:
        """Non-research routes require task_launcher and task_checker."""
        invoker = MagicMock()
        # route_intake returns FULL_PROJECT when spec+plan are present
        intake = IntakeContext(spec="Build an API", plan="Step 1: schema")
        job = create_job("W-1", "My App", intake)

        result = execute_job(job, invoker, task_launcher=None, task_checker=None)
        assert result.status == JobStatus.FAILED
        assert result.error is not None
        assert "task_launcher and task_checker are required" in result.error

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
        job.add_comment("T-1", "Use PostgreSQL instead")
        assert len(job.comments) == 1
        assert job.comments[0] == ("T-1", "Use PostgreSQL instead")

    def test_multiple_comments(self) -> None:
        job = create_job("W-1", "Test", IntakeContext())
        job.add_comment("T-1", "first")
        job.add_comment("T-2", "second")
        assert len(job.comments) == 2

    def test_get_comments_for_task(self) -> None:
        job = create_job("W-1", "Test", IntakeContext())
        job.add_comment("T-1", "feedback for T-1")
        job.add_comment("T-2", "feedback for T-2")
        job.add_comment("T-1", "more feedback for T-1")
        assert job.get_comments_for_task("T-1") == [
            "feedback for T-1",
            "more feedback for T-1",
        ]
        assert job.get_comments_for_task("T-2") == ["feedback for T-2"]
        assert job.get_comments_for_task("T-99") == []


# ---------------------------------------------------------------------------
# Small fix execution path
# ---------------------------------------------------------------------------


class TestSmallFixPath:
    def test_small_fix_skips_decomposition_uses_single_task(self) -> None:
        """Small fix creates a single-task decomposition and skips the CA."""
        invoker = MagicMock()
        # CEO returns small_fix routing
        invoker.invoke.side_effect = [
            {
                "path": "small_fix",
                "reasoning": "Simple one-liner",
                "target_team": "a",
            },
            # Post-PR reviews (QA, Security, Tech Writer)
            {"verdict": "approved", "comments": [], "summary": "ok"},
            {"verdict": "approved", "comments": [], "summary": "ok"},
            {"verdict": "approved", "comments": [], "summary": "ok"},
        ]

        intake = IntakeContext(prompt="Fix the typo in README")
        job = create_job("W-1", "Fix typo", intake)

        def launch(task):  # type: ignore[no-untyped-def]
            return task.id

        def wait(handle):  # type: ignore[no-untyped-def]
            return (True, {"status": "completed"})

        result = execute_job(job, invoker, task_launcher=launch, task_checker=wait)
        assert result.status == JobStatus.COMPLETED
        assert result.decomposition is not None
        assert len(result.decomposition.tasks) == 1
        assert result.decomposition.tasks[0].id == "T-1"
        # CA (decompose) was NOT called -- only CEO routing + post-PR reviews
        # CEO routing is the first invoke call
        assert invoker.invoke.call_count == 4  # routing + 3 post-PR reviews

    def test_small_fix_without_launchers_fails(self) -> None:
        """Small fix still needs task_launcher/task_checker for DAG execution."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "path": "small_fix",
            "reasoning": "Simple fix",
            "target_team": "b",
        }
        intake = IntakeContext(prompt="Fix it")
        job = create_job("W-1", "Fix", intake)

        result = execute_job(job, invoker, task_launcher=None, task_checker=None)
        assert result.status == JobStatus.FAILED
        assert "task_launcher" in (result.error or "")


# ---------------------------------------------------------------------------
# Post-PR review gate enforcement
# ---------------------------------------------------------------------------


class TestReviewGateEnforcement:
    def test_failed_required_gate_blocks_completion(self) -> None:
        """A failing required post-PR review gate transitions job to FAILED."""
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
            # Post-PR QA review -- fails with needs_revision
            {
                "verdict": "needs_revision",
                "comments": [
                    {
                        "file": "api.py",
                        "line": 10,
                        "severity": "error",
                        "comment": "Missing validation",
                    }
                ],
                "summary": "Needs work",
            },
        ]

        intake = IntakeContext(spec="Build an API", plan="Step 1: schema")
        job = create_job("W-1", "My App", intake)

        def launch(task):  # type: ignore[no-untyped-def]
            return task.id

        def wait(handle):  # type: ignore[no-untyped-def]
            return (True, {"status": "completed"})

        result = execute_job(job, invoker, task_launcher=launch, task_checker=wait)
        assert result.status == JobStatus.FAILED
        assert result.error is not None
        assert "Post-PR review failed" in result.error
        assert "qa_review" in result.error


# ---------------------------------------------------------------------------
# Progress tracking after DAG execution
# ---------------------------------------------------------------------------


class TestProgressTracking:
    def test_progress_populated_after_dag(self) -> None:
        """task_results should be populated from DAG results for progress."""
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
                    {
                        "id": "T-2",
                        "description": "Build UI",
                        "assigned_to": "frontend_engineer",
                        "team": "a",
                        "depends_on": [],
                        "pr_group": "feat/ui",
                        "work_type": "code",
                    },
                ],
                "peer_assignments": {
                    "T-1": "frontend_engineer",
                    "T-2": "backend_engineer",
                },
                "parallel_groups": [["T-1", "T-2"]],
            },
            # Post-PR reviews (QA, Security, Tech Writer)
            {"verdict": "approved", "comments": [], "summary": "ok"},
            {"verdict": "approved", "comments": [], "summary": "ok"},
            {"verdict": "approved", "comments": [], "summary": "ok"},
        ]

        intake = IntakeContext(spec="Build an API", plan="Step 1")
        job = create_job("W-1", "My App", intake)

        def launch(task):  # type: ignore[no-untyped-def]
            return task.id

        def wait(handle):  # type: ignore[no-untyped-def]
            return (True, {"status": "completed"})

        result = execute_job(job, invoker, task_launcher=launch, task_checker=wait)
        assert result.status == JobStatus.COMPLETED
        # task_results populated from DAG
        assert "T-1" in result.task_results
        assert "T-2" in result.task_results
        # Progress reflects completed tasks
        completed, total = result.progress
        assert total == 2
        assert completed == 2


# ---------------------------------------------------------------------------
# Shared state machine usage
# ---------------------------------------------------------------------------


class TestSharedStateMachine:
    def test_transition_uses_shared_table(self) -> None:
        """transition_job should use the shared JOB_TRANSITIONS from models.state."""
        from devteam.models.state import JOB_TRANSITIONS

        # Verify the shared table includes PLANNING->EXECUTING (small fix)
        assert JobStatus.EXECUTING in JOB_TRANSITIONS[JobStatus.PLANNING]
        # Verify the shared table includes EXECUTING->COMPLETED (research)
        assert JobStatus.COMPLETED in JOB_TRANSITIONS[JobStatus.EXECUTING]
        # Verify the shared table includes REVIEWING->EXECUTING (revision)
        assert JobStatus.EXECUTING in JOB_TRANSITIONS[JobStatus.REVIEWING]

    def test_transition_job_raises_on_invalid(self) -> None:
        """transition_job wraps InvalidTransitionError as ValueError."""
        job = Job(id="W-1", title="Test", status=JobStatus.COMPLETED)
        with pytest.raises(ValueError, match="Invalid"):
            transition_job(job, JobStatus.PLANNING)
