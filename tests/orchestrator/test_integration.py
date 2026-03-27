"""Integration tests -- full workflow end-to-end with mocked agents.

Verifies that routing, decomposition, DAG execution, task workflows,
review chains, and question escalation all work together correctly.
"""

from unittest.mock import MagicMock

from devteam.models.entities import JobStatus, TaskStatus
from devteam.orchestrator.cli_bridge import (
    JobStore,
    QuestionTracker,
    handle_answer,
    handle_start,
)
from devteam.orchestrator.dag import DAGExecutor, build_dag
from devteam.orchestrator.escalation import escalate_question
from devteam.orchestrator.jobs import (
    Job,
    create_job,
    execute_job,
    transition_job,
)
from devteam.orchestrator.review import execute_post_pr_review
from devteam.orchestrator.routing import IntakeContext
from devteam.orchestrator.schemas import (
    DecompositionResult,
    QuestionRecord,
    QuestionType,
    TaskDecomposition,
    WorkType,
)
from devteam.orchestrator.task_workflow import (
    TaskContext,
    execute_task_workflow,
)


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------


def _impl_ok():  # type: ignore[no-untyped-def]
    return {
        "status": "completed",
        "question": None,
        "files_changed": ["src/api.py"],
        "tests_added": ["tests/test_api.py"],
        "summary": "Built the feature",
        "confidence": "high",
    }


def _review_ok():  # type: ignore[no-untyped-def]
    return {"verdict": "approved", "comments": [], "summary": "LGTM"}


def _review_reject():  # type: ignore[no-untyped-def]
    return {
        "verdict": "needs_revision",
        "comments": [
            {
                "file": "src/api.py",
                "line": 42,
                "severity": "error",
                "comment": "Fix tests",
            },
        ],
        "summary": "Fix tests",
    }


# ---------------------------------------------------------------------------
# TestFullProjectWorkflow
# ---------------------------------------------------------------------------


class TestFullProjectWorkflow:
    """End-to-end: spec+plan -> route -> decompose -> execute -> review."""

    def test_spec_plan_to_completion(self) -> None:
        """Happy path: spec+plan provided, all tasks pass, all reviews pass."""
        invoker = MagicMock()

        # Track which roles are invoked
        invocations: list[str] = []

        def mock_invoke(role, prompt, **kwargs):  # type: ignore[no-untyped-def]
            invocations.append(role)
            if role == "chief_architect":
                return {
                    "tasks": [
                        {
                            "id": "T-1",
                            "description": "Build backend API",
                            "assigned_to": "backend_engineer",
                            "team": "a",
                            "depends_on": [],
                            "pr_group": "feat/api",
                            "work_type": "code",
                        },
                        {
                            "id": "T-2",
                            "description": "Build frontend",
                            "assigned_to": "frontend_engineer",
                            "team": "a",
                            "depends_on": ["T-1"],
                            "pr_group": "feat/ui",
                            "work_type": "code",
                        },
                    ],
                    "peer_assignments": {
                        "T-1": "frontend_engineer",
                        "T-2": "backend_engineer",
                    },
                    "parallel_groups": [["T-1"], ["T-2"]],
                }
            elif role in ("qa_engineer", "security_engineer", "tech_writer"):
                return _review_ok()
            return _review_ok()

        invoker.invoke.side_effect = mock_invoke

        intake = IntakeContext(spec="Build a web app", plan="Step 1: API")
        job = create_job("W-1", "Web App", intake)

        def launch(task):  # type: ignore[no-untyped-def]
            return task.id

        def wait(handle):  # type: ignore[no-untyped-def]
            return (True, {"status": "completed"})

        result = execute_job(job, invoker, task_launcher=launch, task_checker=wait)

        assert result.status == JobStatus.COMPLETED
        # Verify CA was invoked for decomposition
        assert "chief_architect" in invocations
        # Verify post-PR review gates were hit
        assert "qa_engineer" in invocations
        assert "security_engineer" in invocations
        assert "tech_writer" in invocations


# ---------------------------------------------------------------------------
# TestDAGParallelism
# ---------------------------------------------------------------------------


class TestDAGParallelism:
    """Verify that independent tasks are launched in parallel."""

    def test_independent_tasks_launched_together(self) -> None:
        launch_batches: list[list[str]] = []
        current_batch: list[str] = []

        def launch(task):  # type: ignore[no-untyped-def]
            current_batch.append(task.id)
            return task.id

        def wait(handle):  # type: ignore[no-untyped-def]
            nonlocal current_batch
            if current_batch:
                launch_batches.append(list(current_batch))
                current_batch.clear()
            return (True, "ok")

        decomp = DecompositionResult(
            tasks=[
                TaskDecomposition(
                    id="T-1",
                    description="API",
                    assigned_to="backend_engineer",
                    team="a",
                    pr_group="g1",
                ),
                TaskDecomposition(
                    id="T-2",
                    description="UI",
                    assigned_to="frontend_engineer",
                    team="a",
                    pr_group="g2",
                ),
                TaskDecomposition(
                    id="T-3",
                    description="Integration",
                    assigned_to="backend_engineer",
                    team="a",
                    depends_on=["T-1", "T-2"],
                    pr_group="g3",
                ),
            ],
            peer_assignments={},
            parallel_groups=[["T-1", "T-2"], ["T-3"]],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert result.all_succeeded
        # T-1 and T-2 should be in the first batch (launched before any wait)
        assert len(launch_batches) > 0
        first_batch = set(launch_batches[0])
        assert first_batch == {"T-1", "T-2"}, (
            f"Expected both independent tasks in first batch, got {first_batch}"
        )


# ---------------------------------------------------------------------------
# TestReviewChainIntegration
# ---------------------------------------------------------------------------


class TestReviewChainIntegration:
    """Verify review chains are correctly applied based on work type."""

    def test_code_gets_full_review(self) -> None:
        invoker = MagicMock()
        roles_invoked: list[str] = []

        def mock_invoke(role, prompt, **kwargs):  # type: ignore[no-untyped-def]
            roles_invoked.append(role)
            return _review_ok()

        invoker.invoke.side_effect = mock_invoke

        result = execute_post_pr_review(WorkType.CODE, "PR diff here", invoker)
        assert result.all_passed
        assert "qa_engineer" in roles_invoked
        assert "security_engineer" in roles_invoked
        assert "tech_writer" in roles_invoked

    def test_research_gets_ca_only(self) -> None:
        invoker = MagicMock()
        roles_invoked: list[str] = []

        def mock_invoke(role, prompt, **kwargs):  # type: ignore[no-untyped-def]
            roles_invoked.append(role)
            return _review_ok()

        invoker.invoke.side_effect = mock_invoke

        result = execute_post_pr_review(WorkType.RESEARCH, "Research output", invoker)
        assert result.all_passed
        assert roles_invoked == ["chief_architect"]
        assert "qa_engineer" not in roles_invoked


# ---------------------------------------------------------------------------
# TestQuestionEscalationIntegration
# ---------------------------------------------------------------------------


class TestQuestionEscalationIntegration:
    """Verify question handling end-to-end."""

    def test_question_raised_then_answered_by_human(self) -> None:
        store = JobStore()
        job = handle_start(store, title="Test", spec="spec", plan="plan")

        # Simulate engineer raising a question
        q_record = QuestionRecord(
            question="JWT or sessions?",
            question_type=QuestionType.ARCHITECTURAL,
        )
        tracker = QuestionTracker(
            id="Q-1",
            task_id="T-1",
            job_id=job.id,
            record=q_record,
        )
        store.save_question(tracker)

        # Try agent escalation -- all fail
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "resolved": False,
            "reasoning": "Need human input",
        }
        result = escalate_question(q_record, invoker, em_role="em_team_a")
        assert result.needs_human

        # Human answers via CLI bridge
        resolved = handle_answer(store, "Q-1", "Use JWT with refresh tokens")
        assert resolved is not None
        assert resolved.resolved
        assert resolved.answer == "Use JWT with refresh tokens"

        # Verify the tracker was updated
        updated = store.get_question("Q-1")
        assert updated is not None
        assert updated.resolved
        assert updated.answered_by == "human"


# ---------------------------------------------------------------------------
# TestTaskWorkflowIntegration
# ---------------------------------------------------------------------------


class TestTaskWorkflowIntegration:
    """Verify task workflow with review chain enforcement."""

    def test_revision_loop_then_approval(self) -> None:
        """Engineer implements -> peer rejects -> revise -> approve."""
        invoker = MagicMock()
        invoker.invoke.side_effect = [
            # First pass
            _impl_ok(),  # engineer
            _review_reject(),  # peer rejects
            # Revision (peer rejects, so back to engineer without EM)
            _impl_ok(),  # engineer re-implements
            _review_ok(),  # peer approves
            _review_ok(),  # EM approves
        ]

        task = TaskDecomposition(
            id="T-1",
            description="Build API",
            assigned_to="backend_engineer",
            team="a",
            pr_group="g1",
        )
        ctx = TaskContext(
            task=task,
            peer_reviewer="frontend_engineer",
            em_role="em_team_a",
            worktree_path="/tmp/wt",
            job_id="W-1",
        )
        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.APPROVED
        assert result.revision_count == 1


# ---------------------------------------------------------------------------
# TestJobCancellation
# ---------------------------------------------------------------------------


class TestJobCancellation:
    """Verify job can be cancelled from any active state."""

    def test_cancel_executing_job(self) -> None:
        job = Job(id="W-1", title="Test", status=JobStatus.EXECUTING)
        transition_job(job, JobStatus.CANCELED)
        assert job.status == JobStatus.CANCELED

    def test_cancel_reviewing_job(self) -> None:
        job = Job(id="W-1", title="Test", status=JobStatus.REVIEWING)
        transition_job(job, JobStatus.CANCELED)
        assert job.status == JobStatus.CANCELED

    def test_cancel_created_job(self) -> None:
        job = Job(id="W-1", title="Test", status=JobStatus.CREATED)
        transition_job(job, JobStatus.CANCELED)
        assert job.status == JobStatus.CANCELED

    def test_cancel_decomposing_job(self) -> None:
        job = Job(id="W-1", title="Test", status=JobStatus.DECOMPOSING)
        transition_job(job, JobStatus.CANCELED)
        assert job.status == JobStatus.CANCELED


# ---------------------------------------------------------------------------
# TestJobStoreWithCLIBridge
# ---------------------------------------------------------------------------


class TestJobStoreWithCLIBridge:
    """End-to-end store operations through the CLI bridge."""

    def test_create_start_cancel_flow(self) -> None:
        """Create a job, check status, cancel it."""
        from devteam.orchestrator.cli_bridge import handle_cancel, handle_status

        store = JobStore()
        job = handle_start(store, title="My App", spec="build it")

        # Status check
        status = handle_status(store, "W-1")
        assert status["status"] == "created"

        # Cancel
        assert handle_cancel(store, "W-1")
        assert job.cancelled
