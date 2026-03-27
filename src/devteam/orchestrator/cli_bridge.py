"""CLI bridge -- connects CLI commands to the workflow engine.

Handles argument parsing and job creation for:
- devteam start (--spec/--plan/--issue/--prompt)
- devteam comment (inject feedback into running task)
- devteam answer (resolve question, resume paused branch)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from devteam.orchestrator.escalation import EscalationResult, resolve_with_human_answer
from devteam.git.helpers import GitError, git_run
from devteam.git.recovery import check_same_repo_concurrency
from devteam.models.entities import JobStatus
from devteam.orchestrator.jobs import Job, create_job, transition_job
from devteam.orchestrator.routing import IntakeContext
from devteam.orchestrator.schemas import QuestionRecord


# ---------------------------------------------------------------------------
# QuestionTracker -- wraps a QuestionRecord with tracking metadata
# ---------------------------------------------------------------------------


@dataclass
class QuestionTracker:
    """In-memory wrapper that adds tracking fields to a QuestionRecord.

    The ``QuestionRecord`` from ``agents.contracts`` is a pure output
    contract (question text + type + context).  Tracking fields like
    ``id``, ``task_id``, ``job_id``, ``resolved``, and ``answer`` are
    operational concerns that belong in the store layer, not the agent
    contract.  This dataclass bridges the gap until DBOS persistence
    lands.
    """

    id: str
    task_id: str
    job_id: str
    record: QuestionRecord
    resolved: bool = False
    answer: str | None = None
    answered_by: str | None = None


# ---------------------------------------------------------------------------
# JobStore -- in-memory store
# ---------------------------------------------------------------------------


class JobStore:
    """In-memory store with dict-access locking. Callers must coordinate
    mutations to returned Job objects.

    In production, backed by DBOS/SQLite.  This is a minimal interface for
    Plan 3.  Plan 1 provides the actual persistence layer.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._questions: dict[str, QuestionTracker] = {}
        self._next_job_id: int = 1
        self._next_question_id: int = 1

    # -- job helpers ---------------------------------------------------------

    def next_id(self) -> str:
        with self._lock:
            job_id = f"W-{self._next_job_id}"
            self._next_job_id += 1
            return job_id

    def save(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    # -- question helpers ----------------------------------------------------

    def next_question_id(self) -> str:
        with self._lock:
            qid = f"Q-{self._next_question_id}"
            self._next_question_id += 1
            return qid

    def save_question(self, question: QuestionTracker) -> None:
        with self._lock:
            self._questions[question.id] = question

    def get_question(self, question_id: str) -> QuestionTracker | None:
        with self._lock:
            return self._questions.get(question_id)

    def get_pending_questions(self, job_id: str | None = None) -> list[QuestionTracker]:
        with self._lock:
            questions = [q for q in self._questions.values() if not q.resolved]
            if job_id:
                questions = [q for q in questions if q.job_id == job_id]
            return questions


# ---------------------------------------------------------------------------
# parse_intake
# ---------------------------------------------------------------------------


def parse_intake(
    spec: str | None = None,
    plan: str | None = None,
    issue: str | None = None,
    prompt: str | None = None,
) -> IntakeContext:
    """Parse CLI arguments into an IntakeContext.

    Reads file contents for --spec and --plan if they are file paths.
    """
    spec_content = None
    plan_content = None

    if spec:
        spec_path = Path(spec)
        if spec_path.exists():
            spec_content = spec_path.read_text()
        else:
            spec_content = spec

    if plan:
        plan_path = Path(plan)
        if plan_path.exists():
            plan_content = plan_path.read_text()
        else:
            plan_content = plan

    return IntakeContext(
        spec=spec_content,
        plan=plan_content,
        issue_url=issue,
        prompt=prompt,
    )


# ---------------------------------------------------------------------------
# _detect_repo
# ---------------------------------------------------------------------------


def _detect_repo(cwd: Path | None = None) -> str | None:
    """Detect repository identity from the current working directory.

    Tries ``git remote get-url origin`` first (normalised to owner/name),
    then falls back to the repo root directory name.

    Returns:
        A string identifying the repo, or None if detection fails.
    """
    try:
        url = git_run(["remote", "get-url", "origin"], cwd=cwd)
        # Normalise SSH and HTTPS URLs to "owner/repo"
        url = url.rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        # git@github.com:owner/repo (SCP-style SSH)
        if ":" in url and "@" in url and "://" not in url:
            return url.split(":")[-1]
        # HTTPS or ssh:// — take last two path segments
        parts = url.split("/")
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
    except GitError:
        pass
    return None


# ---------------------------------------------------------------------------
# handle_start
# ---------------------------------------------------------------------------


def handle_start(
    store: JobStore,
    title: str = "Untitled Job",
    spec: str | None = None,
    plan: str | None = None,
    issue: str | None = None,
    prompt: str | None = None,
    cwd: Path | None = None,
) -> Job:
    """Handle ``devteam start`` -- create job and prepare for execution.

    Returns the created job.  The caller (daemon) is responsible for
    launching the actual workflow execution.

    Raises:
        ValueError: If another active job already targets the same repo.
    """
    intake = parse_intake(spec=spec, plan=plan, issue=issue, prompt=prompt)

    # Detect repo and check for same-repo concurrency conflicts
    repo = _detect_repo(cwd)
    if not repo:
        import logging

        logging.getLogger(__name__).warning(
            "Could not detect repo identity — same-repo concurrency check skipped"
        )
    # Atomic check-and-save under the store lock to prevent race conditions
    with store._lock:
        if repo:
            active_jobs = [
                {"job_id": j.id, "repo": j.repo}
                for j in store.list_jobs()
                if j.repo and j.status not in TERMINAL_STATES
            ]
            conflict = check_same_repo_concurrency(repo, active_jobs)
            if conflict:
                raise ValueError(
                    f"Repo {repo} already has an active job: {conflict['job_id']}. "
                    "Cancel or complete it before starting another."
                )

        job_id = store.next_id()
        job = create_job(job_id, title, intake)
        job.repo = repo
        store.save(job)
    return job


# ---------------------------------------------------------------------------
# handle_comment
# ---------------------------------------------------------------------------


def handle_comment(
    store: JobStore,
    task_ref: str,
    comment: str,
) -> bool:
    """Handle ``devteam comment`` -- inject feedback into a task.

    Args:
        task_ref: Task reference like 'W-1/T-3' or 'T-3' (single job).
        comment: The feedback text.

    Returns:
        True if comment was attached successfully.
    """
    try:
        job_id, task_id = _parse_task_ref(task_ref, store)
    except ValueError:
        raise  # Propagate ambiguous-ref error to caller
    if not job_id:
        return False

    job = store.get(job_id)
    if not job:
        return False

    job.add_comment(task_id, comment)
    store.save(job)
    return True


# ---------------------------------------------------------------------------
# handle_answer
# ---------------------------------------------------------------------------


def handle_answer(
    store: JobStore,
    question_ref: str,
    answer: str,
) -> EscalationResult | None:
    """Handle ``devteam answer`` -- resolve a question and resume branch.

    Args:
        question_ref: Question reference like 'W-1/Q-3' or 'Q-3'.
        answer: The human's answer.

    Returns:
        The EscalationResult, or None if the question was not found.
    """
    job_id, question_id = _parse_question_ref(question_ref)
    tracker = store.get_question(question_id)
    if not tracker:
        return None
    if job_id and tracker.job_id != job_id:
        raise ValueError(f"Question {question_id} belongs to job {tracker.job_id}, not {job_id}")

    result = resolve_with_human_answer(tracker.record, answer)
    tracker.resolved = True
    tracker.answer = answer
    tracker.answered_by = "human"
    store.save_question(tracker)

    # Store answer on the job for deferred task resumption.
    # Full resume flow requires DBOS workflow replay (Phase 6). For now
    # the answer is recorded so the next execution attempt can pick it up.
    job = store.get(tracker.job_id)
    if job:
        job.pending_answers[tracker.task_id] = answer
        store.save(job)

    return result


# ---------------------------------------------------------------------------
# handle_status
# ---------------------------------------------------------------------------


def handle_status(
    store: JobStore,
    target: str | None = None,
) -> dict[str, object]:
    """Handle ``devteam status`` -- return job/task status from the store.

    Args:
        target: Job ID like 'W-1', or None for all jobs.

    Returns:
        A dict with job status information.
    """
    if target:
        job = store.get(target)
        if not job:
            return {"error": f"Job {target} not found"}
        return {
            "job_id": job.id,
            "title": job.title,
            "status": job.status.value,
            "progress": job.progress,
        }
    # All jobs
    jobs = store.list_jobs()
    return {
        "jobs": [
            {
                "job_id": j.id,
                "title": j.title,
                "status": j.status.value,
                "progress": j.progress,
            }
            for j in jobs
        ]
    }


# ---------------------------------------------------------------------------
# handle_cancel
# ---------------------------------------------------------------------------


TERMINAL_STATES = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED}


def handle_cancel(
    store: JobStore,
    job_id: str,
) -> bool:
    """Handle ``devteam cancel`` -- set cancellation flag on a job.

    Returns True if the job was found and cancelled.
    Returns False if the job was not found or is already in a terminal state.
    """
    job = store.get(job_id)
    if not job:
        return False
    if job.status in TERMINAL_STATES:
        return False  # Already terminal, nothing to cancel
    job.cancelled = True
    transition_job(job, JobStatus.CANCELED)
    store.save(job)
    return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_task_ref(ref: str, store: JobStore) -> tuple[str | None, str]:
    """Parse 'W-1/T-3' or 'T-3' into (job_id, task_id).

    Raises ``ValueError`` when the short form is used but multiple jobs
    are active, making the reference ambiguous.
    """
    if "/" in ref:
        parts = ref.split("/", 1)
        return parts[0], parts[1]
    # Single job shorthand -- find the only active job
    jobs = store.list_jobs()
    if len(jobs) == 1:
        return jobs[0].id, ref
    if len(jobs) > 1:
        raise ValueError("Multiple jobs active. Use W-1/T-3 to specify which job.")
    return None, ref


def _parse_question_ref(ref: str) -> tuple[str | None, str]:
    """Parse 'W-1/Q-3' or 'Q-3' into (job_id, question_id)."""
    if "/" in ref:
        parts = ref.split("/", 1)
        return parts[0], parts[1]
    return None, ref
