"""Chief Architect decomposition workflow — breaks spec+plan into task DAG.

The CA takes a spec+plan and routing decision, then produces a task DAG
with peer assignments, dependencies, and PR groupings. This is the bridge
between routing and execution.
"""

from __future__ import annotations

from devteam.orchestrator.routing import InvokerProtocol
from devteam.orchestrator.schemas import (
    DecompositionResult,
    RoutingResult,
    TaskDecomposition,
)


# Peer review assignment table from the design spec.
# Each team maps engineer roles to their preferred peer reviewers.
PEER_REVIEW_MAP_TEAM_A: dict[str, list[str]] = {
    "backend": ["frontend", "devops"],
    "frontend": ["backend"],
    "devops": ["backend"],
}

PEER_REVIEW_MAP_TEAM_B: dict[str, list[str]] = {
    "data": ["infra"],
    "infra": ["data", "tooling"],
    "tooling": ["infra", "cloud"],
    "cloud": ["infra"],
}


def get_default_peer_reviewer(assigned_to: str, team: str) -> str | None:
    """Return the default peer reviewer for an engineer based on team rules.

    Looks up the assignment table for the given team and returns the first
    candidate. Returns None if no mapping exists.
    """
    if team == "a":
        candidates = PEER_REVIEW_MAP_TEAM_A.get(assigned_to, [])
    elif team == "b":
        candidates = PEER_REVIEW_MAP_TEAM_B.get(assigned_to, [])
    else:
        return None
    return candidates[0] if candidates else None


def build_decomposition_prompt(
    spec: str,
    plan: str,
    routing: RoutingResult,
) -> str:
    """Build the prompt for CA decomposition."""
    return (
        "Decompose the following spec and plan into implementation tasks.\n\n"
        f"## Routing Decision\nPath: {routing.path.value}\n"
        f"Reasoning: {routing.reasoning}\n\n"
        f"## Spec\n{spec}\n\n"
        f"## Plan\n{plan}\n\n"
        "## Instructions\n"
        "- Assign each task to a specific engineer role "
        "(backend, frontend, devops, data, infra, tooling, cloud)\n"
        "- Assign each task to team 'a' or 'b'\n"
        "- Define dependencies between tasks (task IDs)\n"
        "- Group tasks into PR groups (tasks that ship together)\n"
        "- Identify parallel groups (tasks that can run simultaneously)\n"
        "- Maximize parallelism: only add dependencies where truly required\n"
    )


def assign_peer_reviewers(
    tasks: list[TaskDecomposition],
    explicit_assignments: dict[str, str] | None = None,
) -> dict[str, str]:
    """Assign peer reviewers to all tasks.

    Uses explicit CA assignments if provided, otherwise falls back
    to the default peer review map.
    """
    assignments = dict(explicit_assignments or {})
    for task in tasks:
        if task.id not in assignments:
            reviewer = get_default_peer_reviewer(task.assigned_to, task.team)
            if reviewer:
                assignments[task.id] = reviewer
    return assignments


def validate_decomposition(result: DecompositionResult) -> list[str]:
    """Validate a decomposition result for consistency.

    Returns a list of validation errors (empty if valid).
    Note: Pydantic model validators on DecompositionResult already check
    most of these, but this function provides a second layer of validation
    with human-readable error collection (useful when re-validating after
    post-processing like peer assignment backfill).
    """
    errors: list[str] = []
    task_ids = {t.id for t in result.tasks}

    # Check dependency references
    for task in result.tasks:
        for dep in task.depends_on:
            if dep not in task_ids:
                errors.append(f"Task {task.id} depends on unknown task {dep}")

    # Check peer assignments reference valid tasks
    for task_id in result.peer_assignments:
        if task_id not in task_ids:
            errors.append(f"Peer assignment references unknown task {task_id}")

    # Check for circular dependencies (DFS cycle detection)
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def has_cycle(tid: str) -> bool:
        visited.add(tid)
        rec_stack.add(tid)
        task = next((t for t in result.tasks if t.id == tid), None)
        if task:
            for dep in task.depends_on:
                if dep not in visited:
                    if has_cycle(dep):
                        return True
                elif dep in rec_stack:
                    return True
        rec_stack.discard(tid)
        return False

    for task in result.tasks:
        if task.id not in visited:
            if has_cycle(task.id):
                errors.append("Circular dependency detected in task graph")
                break

    # Check parallel groups reference valid tasks
    for group in result.parallel_groups:
        for tid in group:
            if tid not in task_ids:
                errors.append(f"Parallel group references unknown task {tid}")

    return errors


def decompose(
    spec: str,
    plan: str,
    routing: RoutingResult,
    invoker: InvokerProtocol,
) -> DecompositionResult:
    """Invoke CA to decompose spec+plan into task DAG.

    For superpowers specs (already reviewed), CA does decomposition
    only. For raw ideas, CA also validates the spec quality.

    Steps:
    1. Build prompt with spec, plan, and routing context.
    2. Invoke the chief_architect agent.
    3. Parse and validate the DecompositionResult.
    4. Fill in missing peer assignments from default maps.
    5. Run post-processing validation.
    """
    prompt = build_decomposition_prompt(spec, plan, routing)
    raw = invoker.invoke(
        role="chief_architect",
        prompt=prompt,
        json_schema=DecompositionResult.model_json_schema(),
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
