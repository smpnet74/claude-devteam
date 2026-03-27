"""Job lifecycle management -- top-level workflow orchestration.

Manages the full job lifecycle: created -> planning -> decomposing ->
executing -> reviewing -> completed.

This module ties together routing, decomposition, DAG execution,
and post-PR review into a single durable workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from devteam.models.entities import JobStatus, TaskStatus
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
    WorkType,
)
from devteam.orchestrator.task_workflow import TaskWorkflowResult


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
    comments: list[str] = field(default_factory=list)
    cancelled: bool = False

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

    def add_comment(self, comment: str) -> None:
        """Add operator feedback to the job."""
        self.comments.append(comment)


def create_job(
    job_id: str,
    title: str,
    intake: IntakeContext,
) -> Job:
    """Create a new job from intake context."""
    return Job(id=job_id, title=title, intake=intake)


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

# Transition table for the workflow-engine Job (a superset of the persistence
# model's table: adds PLANNING->EXECUTING for small-fix and
# EXECUTING->COMPLETED for research path).
_JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.CREATED: {JobStatus.PLANNING, JobStatus.CANCELED},
    JobStatus.PLANNING: {
        JobStatus.DECOMPOSING,
        JobStatus.EXECUTING,  # small fix skips decomposition
        JobStatus.CANCELED,
        JobStatus.FAILED,
    },
    JobStatus.DECOMPOSING: {
        JobStatus.EXECUTING,
        JobStatus.CANCELED,
        JobStatus.FAILED,
    },
    JobStatus.EXECUTING: {
        JobStatus.REVIEWING,
        JobStatus.COMPLETED,  # research path has no post-PR review
        JobStatus.PAUSED_RATE_LIMIT,
        JobStatus.CANCELED,
        JobStatus.FAILED,
    },
    JobStatus.REVIEWING: {
        JobStatus.COMPLETED,
        JobStatus.EXECUTING,  # revision requested
        JobStatus.CANCELED,
        JobStatus.FAILED,
    },
    JobStatus.PAUSED_RATE_LIMIT: {
        JobStatus.EXECUTING,
        JobStatus.CANCELED,
    },
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELED: set(),
}


def transition_job(job: Job, new_status: JobStatus) -> Job:
    """Transition a job to a new status with validation.

    Enforces valid state transitions per the spec:
    created -> planning -> decomposing -> executing -> reviewing -> completed
    """
    allowed = _JOB_TRANSITIONS.get(job.status, set())
    if new_status not in allowed:
        raise ValueError(f"Invalid transition: {job.status.value} -> {new_status.value}")

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

        # Step 3: Decompose
        transition_job(job, JobStatus.DECOMPOSING)
        spec = job.intake.spec or ""
        plan = job.intake.plan or ""
        job.decomposition = decompose(spec, plan, job.routing, invoker)

        if job.cancelled:
            transition_job(job, JobStatus.CANCELED)
            return job

        # Step 4: Execute DAG
        transition_job(job, JobStatus.EXECUTING)

        if task_launcher and task_checker:
            dag = build_dag(job.decomposition)
            executor = DAGExecutor(
                launch_task=task_launcher,
                check_complete=task_checker,
            )
            job.dag_result = executor.execute(dag)

        if job.cancelled:
            transition_job(job, JobStatus.CANCELED)
            return job

        # Step 5: Post-PR review (route-appropriate)
        if needs_post_pr_review(job.routing.path) and job.dag_result:
            if job.dag_result.all_succeeded:
                transition_job(job, JobStatus.REVIEWING)
                work_type = determine_work_type_from_route(job.routing.path)
                execute_post_pr_review(
                    work_type=work_type,
                    pr_context="PR review context placeholder",
                    invoker=invoker,
                )

        # Step 6: Complete
        if job.dag_result and job.dag_result.all_succeeded:
            transition_job(job, JobStatus.COMPLETED)
        elif job.dag_result:
            transition_job(job, JobStatus.FAILED)
            job.error = "Some tasks failed"

    except Exception as e:
        # Any unhandled error transitions the job to FAILED
        if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED):
            try:
                transition_job(job, JobStatus.FAILED)
            except ValueError:
                # Already in a terminal state
                pass
            job.error = str(e)

    return job
