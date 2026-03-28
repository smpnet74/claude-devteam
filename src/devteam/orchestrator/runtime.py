"""DBOS step wrappers around pure orchestrator logic.

Thin @DBOS.step() functions that bridge pure orchestrator functions
(routing, decomposition, review, git) into DBOS durable workflows.
Module-level singletons (_invoker, etc.) are set by bootstrap at startup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dbos import DBOS

from devteam.agents.invoker import AgentInvoker, InvocationContext
from devteam.git.cleanup import (
    CleanupResult,
    cleanup_after_merge,
    cleanup_single_pr,
)
from devteam.git.pr import PRInfo, create_pr
from devteam.git.worktree import WorktreeInfo, create_worktree
from devteam.knowledge.index import INDEX_EMPTY, build_memory_index_safe
from devteam.orchestrator.decomposition import (
    assign_peer_reviewers,
    build_decomposition_prompt,
    validate_decomposition,
)
from devteam.orchestrator.review import (
    PostPRReviewResult,
    get_review_chain,
    is_small_fix_with_no_behavior_change,
    sanitize_pr_context,
)
from devteam.orchestrator.routing import (
    IntakeContext,
    build_routing_prompt,
    classify_intake,
)
from devteam.orchestrator.schemas import (
    DecompositionResult,
    ReviewResult,
    RoutePath,
    RoutingResult,
    WorkType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons (set by bootstrap)
# ---------------------------------------------------------------------------

_invoker: AgentInvoker | None = None
_knowledge_store: Any = None  # KnowledgeStore | None
_config: dict[str, Any] = {}


def set_invoker(invoker: AgentInvoker | None) -> None:
    """Set the global AgentInvoker (called by bootstrap). Pass None to reset."""
    global _invoker
    _invoker = invoker


def set_knowledge_store(store: Any) -> None:
    """Set the global KnowledgeStore (called by bootstrap)."""
    global _knowledge_store
    _knowledge_store = store


def set_config(config: dict[str, Any]) -> None:
    """Set the global config dict (called by bootstrap)."""
    global _config
    _config = config


# ---------------------------------------------------------------------------
# invoke_agent_step — core step for all agent calls
# ---------------------------------------------------------------------------


@DBOS.step()
async def invoke_agent_step(
    role: str,
    prompt: str,
    worktree_path: str,
    project_name: str,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Invoke an agent via the AgentInvoker and return the result as a dict.

    This is the single DBOS step through which all agent invocations flow.
    DBOS provides automatic retry and durable execution tracking.

    Args:
        role: Agent role slug (e.g. 'ceo', 'backend_engineer').
        prompt: Task-specific prompt to send to the agent.
        worktree_path: Path to the git worktree for this invocation.
        project_name: Name of the current project.
        timeout: Maximum seconds to wait for the agent call.

    Returns:
        Dict of the agent's structured output.

    Raises:
        RuntimeError: If no invoker is configured or invocation fails.
    """
    if _invoker is None:
        raise RuntimeError("No invoker configured. Call set_invoker() during bootstrap.")

    context = InvocationContext(
        worktree_path=Path(worktree_path),
        project_name=project_name,
        timeout=timeout,
    )

    # Optionally inject knowledge context into the prompt.
    # build_memory_index_safe returns INDEX_EMPTY when store is unavailable or empty.
    memory_index = await build_memory_index_safe(_knowledge_store, project_name)
    augmented_prompt = prompt
    if memory_index and memory_index != INDEX_EMPTY:
        augmented_prompt = f"{memory_index}\n\n---\n\n{prompt}"

    try:
        result = await _invoker.invoke(
            role=role,
            task_prompt=augmented_prompt,
            context=context,
        )
    except Exception as e:
        raise RuntimeError(
            f"invoke_agent_step failed for role={role!r}, "
            f"project_name={project_name!r}, worktree_path={worktree_path!r}: {e}"
        ) from e
    # Return dict so DBOS can serialize step results for checkpointing
    return result.model_dump()


# ---------------------------------------------------------------------------
# route_intake_step
# ---------------------------------------------------------------------------


async def route_intake_step(
    ctx: IntakeContext,
    project_name: str = "",
    worktree_path: str = "",
) -> RoutingResult:
    """Route incoming work through fast-path classification or CEO agent.

    Not a DBOS step — called from workflow context. Agent calls go through
    invoke_agent_step which IS a step and gets its own checkpoint.

    Args:
        ctx: Parsed intake context.
        project_name: Current project name for knowledge lookup.
        worktree_path: Repo root path.

    Returns:
        RoutingResult with routing path and reasoning.
    """
    # Try fast-path first
    fast_path = classify_intake(ctx)
    if fast_path == RoutePath.FULL_PROJECT:
        return RoutingResult(
            path=RoutePath.FULL_PROJECT,
            reasoning="Spec and plan provided -- direct to full project workflow",
        )

    # CEO analysis needed
    prompt = build_routing_prompt(ctx)
    raw = await invoke_agent_step(
        role="ceo",
        prompt=prompt,
        worktree_path=worktree_path or ctx.repo_path or "",
        project_name=project_name,
    )
    return RoutingResult.model_validate(raw)


# ---------------------------------------------------------------------------
# decompose_step
# ---------------------------------------------------------------------------


async def decompose_step(
    spec: str,
    plan: str,
    routing: RoutingResult,
    project_name: str = "",
    worktree_path: str = "",
) -> DecompositionResult:
    """Invoke CA to decompose spec+plan into a task DAG.

    Not a DBOS step — called from workflow context. Agent calls go through
    invoke_agent_step which IS a step and gets its own checkpoint.

    Args:
        spec: Project specification text.
        plan: Implementation plan text.
        routing: The routing result that determined this path.
        project_name: Current project name for knowledge lookup.
        worktree_path: Repo root path.

    Returns:
        Validated DecompositionResult with tasks, peer assignments,
        and parallel groups.

    Raises:
        ValueError: If routing path is not FULL_PROJECT or OSS_CONTRIBUTION,
            or if decomposition validation fails.
    """
    if routing.path not in (RoutePath.FULL_PROJECT, RoutePath.OSS_CONTRIBUTION):
        raise ValueError(
            f"decompose_step only supports FULL_PROJECT and OSS_CONTRIBUTION, "
            f"got {routing.path.value}"
        )

    prompt = build_decomposition_prompt(spec, plan, routing)
    raw = await invoke_agent_step(
        role="chief_architect",
        prompt=prompt,
        worktree_path=worktree_path,
        project_name=project_name,
    )
    result = DecompositionResult.model_validate(raw)

    # Fill in missing peer assignments from defaults
    result = result.model_copy(
        update={"peer_assignments": assign_peer_reviewers(result.tasks, result.peer_assignments)}
    )

    # Validate post-processing result
    errors = validate_decomposition(result)
    if errors:
        raise ValueError(f"Decomposition validation failed: {'; '.join(errors)}")

    return result


# ---------------------------------------------------------------------------
# post_pr_review_step
# ---------------------------------------------------------------------------


async def post_pr_review_step(
    work_type: WorkType,
    pr_context: str,
    project_name: str = "",
    worktree_path: str = "",
    files_changed: list[str] | None = None,
    skip_qa_for_no_behavior_change: bool = True,
    assigned_to: str | None = None,
) -> PostPRReviewResult:
    """Execute the post-PR review chain for a work type.

    Not a DBOS step — called from workflow context. Each gate invocation
    goes through invoke_agent_step which IS a step and gets its own checkpoint.

    Args:
        work_type: Type of work being reviewed.
        pr_context: PR diff or description to review.
        project_name: Current project name for knowledge lookup.
        worktree_path: Repo root path.
        files_changed: List of changed file paths (for QA skip heuristic).
        skip_qa_for_no_behavior_change: Skip QA for doc-only changes.
        assigned_to: Task assignee role (for DOCUMENTATION gate override).

    Returns:
        PostPRReviewResult with gate results and pass/fail status.
    """
    pr_context = sanitize_pr_context(pr_context)
    chain = get_review_chain(work_type, assigned_to=assigned_to)

    gate_results: dict[str, ReviewResult] = {}
    failed_gates: list[str] = []
    skipped_gates: list[str] = []

    for gate in chain.gates:
        # Small fix optimization: skip QA if no behavior change
        if (
            skip_qa_for_no_behavior_change
            and gate.name == "qa_review"
            and files_changed
            and is_small_fix_with_no_behavior_change(work_type, files_changed)
        ):
            skipped_gates.append(gate.name)
            continue

        try:
            raw = await invoke_agent_step(
                role=gate.reviewer_role,
                prompt=(
                    f"## {gate.name.replace('_', ' ').title()}\n\n"
                    f"{pr_context}\n\n"
                    "Review and provide your verdict.\n"
                ),
                worktree_path=worktree_path,
                project_name=project_name,
            )
        except Exception as e:
            if not gate.required:
                failed_gates.append(gate.name)
                continue
            raise RuntimeError(f"Post-PR review gate '{gate.name}' invocation failed: {e}") from e

        try:
            result = ReviewResult.model_validate(raw)
        except Exception as e:
            if not gate.required:
                failed_gates.append(gate.name)
                continue
            raise RuntimeError(
                f"Post-PR review gate '{gate.name}' returned invalid payload: {e}"
            ) from e

        gate_results[gate.name] = result

        if result.needs_revision:
            failed_gates.append(gate.name)
            if gate.required:
                break

    # all_passed is True if no REQUIRED gates failed
    required_gate_names = {g.name for g in chain.gates if g.required}
    required_failures = [g for g in failed_gates if g in required_gate_names]

    return PostPRReviewResult(
        all_passed=len(required_failures) == 0,
        gate_results=gate_results,
        failed_gates=failed_gates,
        skipped_gates=skipped_gates,
    )


# ---------------------------------------------------------------------------
# create_worktree_step
# ---------------------------------------------------------------------------


@DBOS.step()
async def create_worktree_step(
    repo_root: Path,
    branch: str,
    base_ref: str | None = None,
) -> WorktreeInfo:
    """Create a git worktree for a branch.

    Args:
        repo_root: Root of the main git repo.
        branch: Branch name to create.
        base_ref: Git ref to branch from (default HEAD).

    Returns:
        WorktreeInfo with path and branch info.
    """
    return create_worktree(repo_root, branch, base_ref=base_ref)


# ---------------------------------------------------------------------------
# create_pr_step
# ---------------------------------------------------------------------------


@DBOS.step()
async def create_pr_step(
    cwd: Path,
    title: str,
    body: str,
    branch: str,
    base: str = "main",
    upstream_repo: str | None = None,
) -> PRInfo:
    """Create a pull request via gh CLI.

    Idempotent: if a PR already exists for the branch, returns it.

    Args:
        cwd: Working directory (must be in a git repo).
        title: PR title.
        body: PR body/description.
        branch: Head branch name.
        base: Base branch to merge into.
        upstream_repo: If working from a fork, the upstream 'owner/name'.

    Returns:
        PRInfo with number and URL.
    """
    return create_pr(
        cwd=cwd,
        title=title,
        body=body,
        branch=branch,
        base=base,
        upstream_repo=upstream_repo,
    )


# ---------------------------------------------------------------------------
# cleanup_step
# ---------------------------------------------------------------------------


@DBOS.step()
async def cleanup_step(
    repo_root: Path,
    branch: str,
    mode: str = "merge",
    worktree_path: Path | None = None,
    pr_number: int | None = None,
    comment: str = "Cancelled by operator",
) -> CleanupResult:
    """Clean up git artifacts (worktree, branches, PRs).

    Args:
        repo_root: Root of the main git repo.
        branch: Branch name.
        mode: 'merge' for post-merge cleanup, 'cancel' for cancellation.
        worktree_path: Path to the worktree (if it exists).
        pr_number: PR number (for cancel mode).
        comment: Comment to post on closed PRs (cancel mode).

    Returns:
        CleanupResult with actions taken and any errors.
    """
    if mode == "merge":
        return cleanup_after_merge(
            repo_root=repo_root,
            branch=branch,
            worktree_path=worktree_path,
        )
    elif mode == "cancel":
        if pr_number is None:
            raise ValueError("cleanup_step in 'cancel' mode requires pr_number")
        return cleanup_single_pr(
            repo_root=repo_root,
            branch=branch,
            pr_number=pr_number,
            worktree_path=worktree_path,
            comment=comment,
        )
    else:
        raise ValueError(f"Unknown cleanup mode: {mode!r}. Use 'merge' or 'cancel'.")
