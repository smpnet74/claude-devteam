"""Job lifecycle management -- top-level workflow orchestration.

Manages the full job lifecycle: created -> planning -> decomposing ->
executing -> reviewing -> completed.

This module ties together routing, decomposition, DAG execution,
and post-PR review into a single durable workflow.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from devteam.models.entities import JobStatus, TaskStatus
from devteam.models.state import InvalidTransitionError, validate_job_transition
from devteam.orchestrator.dag import (
    DAGExecutionResult,
    DAGExecutor,
    build_dag,
)
from devteam.orchestrator.decomposition import decompose
from devteam.orchestrator.review import execute_post_pr_review
from devteam.orchestrator.routing import (
    IntakeContext,
    InvokerProtocol,
    route_intake,
)
from devteam.orchestrator.schemas import (
    DecompositionResult,
    RoutePath,
    RoutingResult,
    TaskDecomposition,
    WorkType,
)
from devteam.orchestrator.task_workflow import TaskWorkflowResult


# TODO(persistence): This Job dataclass uses `id` while models.entities.Job uses
# `job_id` with W-N format validation. When DBOS persistence is wired in,
# reconcile these two classes. The persistence model also enforces Priority enum
# and has spec_path/plan_path instead of the IntakeContext stored here.


@dataclass
class Job:
    """A top-level job with its full workflow state.

    This is the workflow-engine's runtime representation of a job.  It is
    deliberately a plain dataclass (not a Pydantic model) because it holds
    references to other workflow objects (DAGExecutionResult, TaskWorkflowResult)
    that are not persisted.  The Pydantic ``devteam.models.entities.Job`` is
    the persistence model; this class will be reconciled with it when DBOS
    persistence is wired in.
    """

    id: str
    title: str
    status: JobStatus = JobStatus.CREATED
    intake: IntakeContext | None = None
    routing: RoutingResult | None = None
    decomposition: DecompositionResult | None = None
    task_results: dict[str, TaskWorkflowResult] = field(default_factory=dict)
    dag_result: DAGExecutionResult | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    error: str | None = None
    comments: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False
    pending_answers: dict[str, str] = field(default_factory=dict)

    @property
    def progress(self) -> tuple[int, int]:
        """Return (completed, total) task count."""
        if not self.decomposition:
            return (0, 0)
        total = len(self.decomposition.tasks)
        completed = sum(
            1
            for r in self.task_results.values()
            if r.status in (TaskStatus.APPROVED, TaskStatus.COMPLETED)
        )
        return (completed, total)

    def add_comment(self, task_id: str, comment: str) -> None:
        """Add operator feedback associated with a task."""
        self.comments.append((task_id, comment))

    def get_comments_for_task(self, task_id: str) -> list[str]:
        """Return all comments targeting a specific task."""
        return [msg for tid, msg in self.comments if tid == task_id]


def create_job(
    job_id: str,
    title: str,
    intake: IntakeContext,
) -> Job:
    """Create a new job from intake context."""
    return Job(id=job_id, title=title, intake=intake)


# ---------------------------------------------------------------------------
# State transitions -- delegates to the shared state machine in models.state
# ---------------------------------------------------------------------------


def transition_job(job: Job, new_status: JobStatus) -> Job:
    """Transition a job to a new status with validation.

    Uses the shared JOB_TRANSITIONS table from ``models.state`` as the
    single source of truth. Raises ``ValueError`` on invalid transitions
    (wraps ``InvalidTransitionError`` for backward compatibility).
    """
    try:
        validate_job_transition(job.status, new_status)
    except InvalidTransitionError as exc:
        raise ValueError(str(exc)) from exc

    job.status = new_status
    if new_status == JobStatus.COMPLETED:
        job.completed_at = datetime.now(timezone.utc)
    return job


# ---------------------------------------------------------------------------
# Route helpers
# ---------------------------------------------------------------------------


def determine_work_type_from_route(route: RoutePath) -> WorkType:
    """Map routing path to primary work type for review chain selection."""
    mapping = {
        RoutePath.FULL_PROJECT: WorkType.CODE,
        RoutePath.RESEARCH: WorkType.RESEARCH,
        RoutePath.SMALL_FIX: WorkType.CODE,
        RoutePath.OSS_CONTRIBUTION: WorkType.CODE,
    }
    return mapping.get(route, WorkType.CODE)


def needs_post_pr_review(route: RoutePath) -> bool:
    """Determine if this route requires post-PR review gates."""
    return route in (
        RoutePath.FULL_PROJECT,
        RoutePath.SMALL_FIX,
        RoutePath.OSS_CONTRIBUTION,
    )


# ---------------------------------------------------------------------------
# Main job execution
# ---------------------------------------------------------------------------


def execute_job(
    job: Job,
    invoker: InvokerProtocol,
    task_launcher: Any | None = None,
    task_checker: Any | None = None,
) -> Job:
    """Execute the full job workflow.

    Orchestrates: routing -> decomposition -> DAG execution -> post-PR review.

    In production, task_launcher and task_checker are DBOS workflow handles.
    In tests, they are synchronous functions that return immediately.

    Args:
        job: The job to execute.
        invoker: Agent invoker for all agent calls.
        task_launcher: Function to launch a task workflow (returns handle).
        task_checker: Non-blocking check.  Returns (done, result_or_error).
    """
    # TODO(DBOS): When DBOS durable workflows are integrated, this function
    # should be decorated with @DBOS.workflow() and each major step (route,
    # decompose, DAG execute, post-PR review) should be a @DBOS.step() for
    # crash-safe replay. The current implementation is synchronous and
    # non-durable.
    if not job.intake:
        raise ValueError("Job has no intake context")

    try:
        # Check cancellation before each major step
        if job.cancelled:
            transition_job(job, JobStatus.CANCELED)
            return job

        # Step 1: Route
        transition_job(job, JobStatus.PLANNING)
        job.routing = route_intake(job.intake, invoker)

        if job.cancelled:
            transition_job(job, JobStatus.CANCELED)
            return job

        # Step 2: Research path -- single-task, no decomposition/DAG
        if job.routing.path == RoutePath.RESEARCH:
            transition_job(job, JobStatus.EXECUTING)
            transition_job(job, JobStatus.COMPLETED)
            return job

        # Step 3: Small fix path -- skip decomposition, create single task
        if job.routing.path == RoutePath.SMALL_FIX:
            assert job.routing.target_team in ("a", "b"), (
                "RoutingResult validator guarantees target_team for SMALL_FIX"
            )
            target_team: Literal["a", "b"] = job.routing.target_team
            single_task = TaskDecomposition(
                id="T-1",
                description=job.intake.prompt or job.intake.spec or "Small fix",
                assigned_to="backend_engineer",
                team=target_team,
                depends_on=[],
                pr_group="fix/small-fix",
            )
            job.decomposition = DecompositionResult(
                tasks=[single_task],
                peer_assignments={},
                parallel_groups=[["T-1"]],
            )
            # Skip decomposing state -- go directly to executing
            transition_job(job, JobStatus.EXECUTING)
        else:
            # Step 3b: Full project / OSS -- decompose via CA
            if not task_launcher or not task_checker:
                raise ValueError(
                    "task_launcher and task_checker are required for non-research routes"
                )

            transition_job(job, JobStatus.DECOMPOSING)
            spec = job.intake.spec or ""
            plan = job.intake.plan or ""
            job.decomposition = decompose(spec, plan, job.routing, invoker)

        if job.cancelled:
            transition_job(job, JobStatus.CANCELED)
            return job

        if not task_launcher or not task_checker:
            raise ValueError("task_launcher and task_checker are required for non-research routes")

        # Step 4: Execute DAG
        if job.status != JobStatus.EXECUTING:
            transition_job(job, JobStatus.EXECUTING)

        dag = build_dag(job.decomposition)
        executor = DAGExecutor(
            launch_task=task_launcher,
            check_complete=task_checker,
        )
        job.dag_result = executor.execute(dag)

        # Populate task_results from DAG completion for progress tracking
        if job.dag_result:
            for task_id, result in job.dag_result.results.items():
                if isinstance(result, TaskWorkflowResult):
                    job.task_results[task_id] = result
                else:
                    # Wrap raw results as completed TaskWorkflowResult
                    job.task_results[task_id] = TaskWorkflowResult(
                        task_id=task_id,
                        status=TaskStatus.COMPLETED,
                    )
            for task_id in job.dag_result.failed_tasks:
                if task_id not in job.task_results:
                    job.task_results[task_id] = TaskWorkflowResult(
                        task_id=task_id,
                        status=TaskStatus.FAILED,
                        error=job.dag_result.failed_tasks[task_id],
                    )

        if job.cancelled:
            transition_job(job, JobStatus.CANCELED)
            return job

        # Step 5: Post-PR review (route-appropriate)
        if needs_post_pr_review(job.routing.path) and job.dag_result:
            if job.dag_result.all_succeeded:
                transition_job(job, JobStatus.REVIEWING)
                work_type = determine_work_type_from_route(job.routing.path)
                review_result = execute_post_pr_review(
                    work_type=work_type,
                    pr_context="PR review context placeholder",
                    invoker=invoker,
                )
                if not review_result.all_passed:
                    job.error = f"Post-PR review failed: {', '.join(review_result.failed_gates)}"
                    transition_job(job, JobStatus.FAILED)
                    return job

        # Step 6: Complete
        if job.dag_result and job.dag_result.all_succeeded:
            transition_job(job, JobStatus.COMPLETED)
        elif job.dag_result:
            transition_job(job, JobStatus.FAILED)
            job.error = job.error or "Some tasks failed"

    except Exception:
        # Any unhandled error transitions the job to FAILED
        if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED):
            try:
                transition_job(job, JobStatus.FAILED)
            except ValueError:
                # Already in a terminal state
                pass
            job.error = traceback.format_exc()

    return job
