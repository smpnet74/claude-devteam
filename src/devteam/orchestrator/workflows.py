"""DBOS workflow definitions for job and task execution.

execute_task: Child workflow — engineer → peer review → EM review → PR.
execute_job: Parent workflow — route → decompose → DAG → post-PR review (Task 6).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dbos import DBOS

from devteam.orchestrator.runtime import (
    create_pr_step,
    create_worktree_step,
    invoke_agent_step,
)
from devteam.orchestrator.schemas import (
    ImplementationResult,
    ReviewResult,
    TaskDecomposition,
)

logger = logging.getLogger(__name__)

MAX_REVISIONS = 3


@DBOS.workflow()
async def execute_task(
    task: dict[str, Any],
    job_alias: str,
    project_name: str,
    repo_root: str,
    peer_reviewer: str | None = None,
    em_role: str = "em_team_a",
) -> dict[str, Any]:
    """Execute a single task: implement → peer review → EM review → PR.

    Args:
        task: TaskDecomposition as a dict (DBOS-serializable).
        job_alias: Parent job alias (e.g., "W-1").
        project_name: Current project name.
        repo_root: Path to the repo root.
        peer_reviewer: Role slug for peer reviewer (e.g., "frontend_engineer").
        em_role: Role slug for EM reviewer.

    Returns:
        Dict with task result: status, pr_info, and revision count.
    """
    td = TaskDecomposition.model_validate(task)

    # Create worktree (repo_root is str for DBOS serialization, convert to Path)
    branch = f"devteam/{td.pr_group}/{td.id}".replace(" ", "-")
    wt_info = await create_worktree_step(
        repo_root=Path(repo_root),
        branch=branch,
    )
    worktree_path = str(wt_info.path)

    # Register artifact in runtime state (lazy import to avoid circular dep with bootstrap)
    from devteam.orchestrator.bootstrap import get_runtime_store

    store = get_runtime_store()

    # Update task status to running
    store.update_task_status(td.id, "running")

    store.register_artifact(
        task_alias=td.id,
        worktree_path=worktree_path,
        branch_name=branch,
    )

    # Revision loop
    # TODO(Task 6): Add pause/cancel checks via DBOS.recv(topic="control:pause", timeout_seconds=0)
    question_count = 0
    previous_answers: list[str] = []
    for revision in range(MAX_REVISIONS):
        # Build prompt with any prior Q&A context
        base_prompt = f"Implement task {td.id}: {td.description}"
        if previous_answers:
            qa_context = "\n".join(f"- Q&A: {a}" for a in previous_answers)
            base_prompt = f"{base_prompt}\n\nPrevious clarifications:\n{qa_context}"

        # Engineer implementation
        try:
            impl_raw = await invoke_agent_step(
                role=td.assigned_to,
                prompt=base_prompt,
                worktree_path=worktree_path,
                project_name=project_name,
            )
        except Exception as e:
            raise RuntimeError(
                f"Agent invocation failed for task {td.id} (role={td.assigned_to}): {e}"
            ) from e
        impl = ImplementationResult.model_validate(impl_raw)

        # Handle questions
        if impl.status in ("needs_clarification", "blocked"):
            question_text = impl.question or f"Task {td.id} is {impl.status}"
            question_count += 1
            q_topic = f"answer:{td.id}-Q{question_count}"

            # Register question in runtime state
            display = store.register_question(
                internal_id=f"Q-{td.id}-{question_count}",
                child_workflow_id=DBOS.workflow_id or "unknown",
                task_alias=td.id,
                text=question_text,
                tier=2,
            )

            # Signal question to parent and wait for answer
            DBOS.set_event(f"question:{display}", question_text)
            answer = DBOS.recv(q_topic, timeout_seconds=3600)

            if answer:
                store.resolve_question(display)
                previous_answers.append(f"{question_text} → {answer}")
            else:
                # Timeout — mark task as blocked and exit
                store.update_task_status(td.id, "blocked")
                return {
                    "status": "blocked",
                    "task_id": td.id,
                    "revisions": revision + 1,
                    "reason": f"Question {display} timed out without answer",
                }
            continue

        if impl.status != "completed":
            continue

        # Peer review (if peer reviewer assigned)
        if peer_reviewer:
            review_raw = await invoke_agent_step(
                role=peer_reviewer,
                prompt=f"Review implementation of task {td.id}: {td.description}",
                worktree_path=worktree_path,
                project_name=project_name,
            )
            review = ReviewResult.model_validate(review_raw)
            if review.needs_revision:
                logger.info(
                    "Peer review requires revision for %s (attempt %d)", td.id, revision + 1
                )
                continue

        # EM review
        em_raw = await invoke_agent_step(
            role=em_role,
            prompt=f"EM review of task {td.id}: {td.description}",
            worktree_path=worktree_path,
            project_name=project_name,
        )
        em_review = ReviewResult.model_validate(em_raw)
        if em_review.needs_revision:
            logger.info("EM review requires revision for %s (attempt %d)", td.id, revision + 1)
            continue

        # All reviews passed — create PR
        pr_info = await create_pr_step(
            cwd=wt_info.path,
            title=f"[{td.id}] {td.description[:60]}",
            body=f"Automated PR for task {td.id}\nAssigned to: {td.assigned_to}",
            branch=branch,
        )

        store.update_task_status(td.id, "completed")
        return {
            "status": "completed",
            "task_id": td.id,
            "revisions": revision + 1,
            "pr_number": pr_info.number,
            "pr_url": pr_info.url,
        }

    # Exhausted revisions
    store.update_task_status(td.id, "failed")
    return {
        "status": "max_revisions_exceeded",
        "task_id": td.id,
        "revisions": MAX_REVISIONS,
    }


@DBOS.workflow()
async def execute_job(
    spec: str,
    plan: str,
    project_name: str,
    repo_root: str,
) -> dict[str, Any]:
    """Execute a full job: route → decompose → DAG → post-PR review.

    Placeholder — will be fully implemented in Task 6.
    """
    return {"status": "not_implemented", "message": "execute_job is a Task 6 deliverable"}
