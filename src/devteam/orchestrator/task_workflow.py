"""Single task execution workflow -- engineer + review chain.

Enforces: engineer_execute -> peer_review -> em_review
with revision loop on rejection.
"""

from __future__ import annotations

from dataclasses import dataclass

from devteam.models.entities import TaskStatus
from devteam.orchestrator.escalation import escalate_question
from devteam.orchestrator.routing import InvokerProtocol
from devteam.orchestrator.schemas import (
    ImplementationResult,
    QuestionRecord,
    QuestionType,
    ReviewResult,
    TaskDecomposition,
)


MAX_REVISION_ITERATIONS = 3


@dataclass
class TaskContext:
    """Runtime context for a task execution."""

    task: TaskDecomposition
    peer_reviewer: str
    em_role: str
    worktree_path: str
    job_id: str
    spec_context: str = ""
    feedback: str | None = None  # injected human comment


@dataclass
class TaskWorkflowResult:
    """Full result of a task workflow execution."""

    task_id: str
    status: TaskStatus
    implementation: ImplementationResult | None = None
    peer_review: ReviewResult | None = None
    em_review: ReviewResult | None = None
    revision_count: int = 0
    question: QuestionRecord | None = None
    error: str | None = None


def build_implementation_prompt(
    ctx: TaskContext,
    revision_feedback: str | None = None,
) -> str:
    """Build prompt for engineer execution step."""
    parts = [f"## Your Assignment\n{ctx.task.description}\n"]

    if ctx.spec_context:
        parts.append(f"## Spec Context\n{ctx.spec_context}\n")

    if ctx.feedback:
        parts.append(f"## Operator Feedback\n{ctx.feedback}\n")

    if revision_feedback:
        parts.append(
            f"## Revision Required\n"
            f"Your previous implementation was rejected. "
            f"Address this feedback:\n{revision_feedback}\n"
        )

    parts.append(
        "## Instructions\n"
        "If anything is unclear, state your question clearly and stop. "
        "Do not guess or assume.\n"
    )
    return "\n".join(parts)


def build_review_prompt(
    task: TaskDecomposition,
    implementation: ImplementationResult,
    review_type: str,
) -> str:
    """Build prompt for peer or EM review."""
    return (
        f"## Review Request ({review_type})\n\n"
        f"### Task\n{task.description}\n\n"
        f"### Implementation Summary\n{implementation.summary}\n\n"
        f"### Files Changed\n" + "\n".join(f"- {f}" for f in implementation.files_changed) + "\n\n"
        "### Tests Added\n" + "\n".join(f"- {f}" for f in implementation.tests_added) + "\n\n"
        f"### Confidence\n{implementation.confidence}\n\n"
        "Review the implementation in the worktree. "
        "Check for correctness, test coverage, and adherence to project conventions.\n"
    )


def engineer_execute(
    ctx: TaskContext,
    invoker: InvokerProtocol,
    revision_feedback: str | None = None,
) -> ImplementationResult:
    """Execute the engineering step -- invoke the assigned engineer."""
    prompt = build_implementation_prompt(ctx, revision_feedback)
    try:
        raw = invoker.invoke(
            role=ctx.task.assigned_to,
            prompt=prompt,
            json_schema=ImplementationResult.model_json_schema(),
            cwd=ctx.worktree_path,
        )
    except Exception as e:
        raise RuntimeError(f"Engineer execution failed: {e}") from e
    return ImplementationResult.model_validate(raw)


def peer_review(
    ctx: TaskContext,
    implementation: ImplementationResult,
    invoker: InvokerProtocol,
) -> ReviewResult:
    """Execute peer review step."""
    prompt = build_review_prompt(ctx.task, implementation, "Peer Review")
    try:
        raw = invoker.invoke(
            role=ctx.peer_reviewer,
            prompt=prompt,
            json_schema=ReviewResult.model_json_schema(),
            cwd=ctx.worktree_path,
        )
    except Exception as e:
        raise RuntimeError(f"Peer review invocation failed: {e}") from e
    return ReviewResult.model_validate(raw)


def em_review(
    ctx: TaskContext,
    implementation: ImplementationResult,
    peer_result: ReviewResult,
    invoker: InvokerProtocol,
) -> ReviewResult:
    """Execute EM review step."""
    prompt = (
        build_review_prompt(ctx.task, implementation, "EM Review")
        + f"\n### Peer Review Verdict\n{peer_result.verdict}\n"
        f"### Peer Review Summary\n{peer_result.summary}\n"
    )
    try:
        raw = invoker.invoke(
            role=ctx.em_role,
            prompt=prompt,
            json_schema=ReviewResult.model_json_schema(),
            cwd=ctx.worktree_path,
        )
    except Exception as e:
        raise RuntimeError(f"EM review invocation failed: {e}") from e
    return ReviewResult.model_validate(raw)


def _build_revision_feedback(review: ReviewResult) -> str:
    """Build structured revision feedback from a review result."""
    parts = [f"Review summary: {review.summary}"]
    for comment in review.comments:
        parts.append(f"  {comment.file}:{comment.line} [{comment.severity}] {comment.comment}")
    return "\n".join(parts)


def execute_task_workflow(
    ctx: TaskContext,
    invoker: InvokerProtocol,
    max_revisions: int = MAX_REVISION_ITERATIONS,
) -> TaskWorkflowResult:
    """Execute full task workflow: implement -> peer review -> EM review.

    Revision loop: if EM rejects, engineer re-implements with feedback,
    then goes through peer + EM review again. Circuit breaker after
    max_revisions iterations.
    """
    result = TaskWorkflowResult(task_id=ctx.task.id, status=TaskStatus.EXECUTING)
    revision_feedback: str | None = None

    for iteration in range(max_revisions + 1):
        # Step 1: Engineer executes
        impl = engineer_execute(ctx, invoker, revision_feedback)
        result.implementation = impl
        result.revision_count = iteration

        # Check if engineer raised a question
        if impl.status in ("needs_clarification", "blocked"):
            q_type = QuestionType.BLOCKED if impl.status == "blocked" else QuestionType.TECHNICAL
            question = QuestionRecord(
                question=impl.question or "Unspecified question",
                question_type=q_type,
                context=f"Raised during iteration {iteration} of task {ctx.task.id}",
            )

            # Attempt escalation before giving up
            esc_result = escalate_question(question, invoker, em_role=ctx.em_role)

            if esc_result.resolved and esc_result.answer:
                # Feed the answer back as revision feedback and re-execute
                revision_feedback = (
                    f"Your question was answered by escalation "
                    f"({esc_result.final_level.value}): {esc_result.answer}"
                )
                continue

            # Escalation could not resolve -- needs human input
            result.status = TaskStatus.WAITING_ON_QUESTION
            result.question = question
            return result

        # Step 2: Peer review (enforced before EM review)
        result.status = TaskStatus.WAITING_ON_REVIEW
        pr = peer_review(ctx, impl, invoker)
        result.peer_review = pr

        # If peer review requires revision (needs_revision or blocked),
        # don't proceed to EM -- loop back to the engineer
        if pr.needs_revision:
            revision_feedback = _build_revision_feedback(pr)
            result.status = TaskStatus.REVISION_REQUESTED
            continue

        # Step 3: EM review
        em = em_review(ctx, impl, pr, invoker)
        result.em_review = em

        if not em.needs_revision:
            # Approved!
            result.status = TaskStatus.APPROVED
            return result

        # EM requested revision -- loop back
        revision_feedback = _build_revision_feedback(em)
        result.status = TaskStatus.REVISION_REQUESTED

    # Circuit breaker -- max revisions exceeded
    result.status = TaskStatus.FAILED
    result.error = f"Task {ctx.task.id} failed after {max_revisions} revision iterations"
    return result
