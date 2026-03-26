"""Chief Architect decomposition workflow — breaks spec+plan into task DAG.

The CA takes a spec+plan and routing decision, then produces a task DAG
with peer assignments, dependencies, and PR groupings. This is the bridge
between routing and execution.
"""

from __future__ import annotations

from devteam.orchestrator.routing import InvokerProtocol
from devteam.orchestrator.schemas import (
    DecompositionResult,
    RoutePath,
    RoutingResult,
    TaskDecomposition,
)


# All 16 valid agent role slugs (derived from src/devteam/templates/agents/*.md).
VALID_AGENT_ROLES: frozenset[str] = frozenset(
    {
        "backend_engineer",
        "ceo",
        "chief_architect",
        "cloud_engineer",
        "data_engineer",
        "devops_engineer",
        "em_team_a",
        "em_team_b",
        "frontend_engineer",
        "infra_engineer",
        "planner_researcher_a",
        "planner_researcher_b",
        "qa_engineer",
        "security_engineer",
        "tech_writer",
        "tooling_engineer",
    }
)

# Roles that can be assigned tasks by the CA during decomposition.
# Excludes executive/management roles (ceo, chief_architect, em_*) and
# cross-cutting roles (qa_engineer, security_engineer, tech_writer) that
# are invoked by the orchestrator directly, not assigned decomposition tasks.
ASSIGNABLE_ROLES: frozenset[str] = frozenset(
    {
        "backend_engineer",
        "cloud_engineer",
        "data_engineer",
        "devops_engineer",
        "frontend_engineer",
        "infra_engineer",
        "planner_researcher_a",
        "planner_researcher_b",
        "tooling_engineer",
    }
)

# Peer review assignment table from the design spec.
# Each team maps engineer roles to their preferred peer reviewers.
PEER_REVIEW_MAP_TEAM_A: dict[str, list[str]] = {
    "backend_engineer": ["frontend_engineer", "devops_engineer"],
    "frontend_engineer": ["backend_engineer"],
    "devops_engineer": ["backend_engineer"],
}

PEER_REVIEW_MAP_TEAM_B: dict[str, list[str]] = {
    "data_engineer": ["infra_engineer"],
    "infra_engineer": ["data_engineer", "tooling_engineer"],
    "tooling_engineer": ["infra_engineer", "cloud_engineer"],
    "cloud_engineer": ["infra_engineer"],
}

# Set of valid reviewer roles (all values in both peer review maps).
VALID_REVIEWER_ROLES_TEAM_A: frozenset[str] = frozenset(
    role for candidates in PEER_REVIEW_MAP_TEAM_A.values() for role in candidates
)
VALID_REVIEWER_ROLES_TEAM_B: frozenset[str] = frozenset(
    role for candidates in PEER_REVIEW_MAP_TEAM_B.values() for role in candidates
)


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
        "(backend_engineer, frontend_engineer, devops_engineer, "
        "data_engineer, infra_engineer, tooling_engineer, cloud_engineer)\n"
        "- Assign each task to team 'a' or 'b'\n"
        "- Set work_type for each task: code, research, planning, architecture, or documentation\n"
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
    to the default peer review map. Validates that all reviewer roles
    are valid agent slugs.
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
    task_by_id = {t.id: t for t in result.tasks}

    # Check dependency references
    for task in result.tasks:
        for dep in task.depends_on:
            if dep not in task_ids:
                errors.append(f"Task {task.id} depends on unknown task {dep}")

    # Validate assigned_to is an assignable role (not executive/management)
    for task in result.tasks:
        if task.assigned_to not in ASSIGNABLE_ROLES:
            errors.append(f"Task {task.id} assigned to non-assignable role '{task.assigned_to}'")

    # Check peer assignments reference valid tasks
    for task_id in result.peer_assignments:
        if task_id not in task_ids:
            errors.append(f"Peer assignment references unknown task {task_id}")

    # Check peer reviewer != assignee and reviewer is a valid role for the team
    for task_id, reviewer in result.peer_assignments.items():
        task = task_by_id.get(task_id)
        if task is None:
            continue  # already caught above
        if reviewer == task.assigned_to:
            errors.append(f"Task {task_id}: peer reviewer '{reviewer}' is the same as assignee")
        valid_reviewers = (
            VALID_REVIEWER_ROLES_TEAM_A if task.team == "a" else VALID_REVIEWER_ROLES_TEAM_B
        )
        if reviewer not in valid_reviewers:
            errors.append(
                f"Task {task_id}: peer reviewer '{reviewer}' is not a valid "
                f"reviewer for team {task.team}"
            )

    # Check for circular dependencies (DFS cycle detection)
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def has_cycle(tid: str) -> bool:
        visited.add(tid)
        rec_stack.add(tid)
        task = task_by_id.get(tid)
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
    if routing.path not in (RoutePath.FULL_PROJECT, RoutePath.OSS_CONTRIBUTION):
        raise ValueError(
            f"decompose() only supports FULL_PROJECT and OSS_CONTRIBUTION, got {routing.path.value}"
        )

    prompt = build_decomposition_prompt(spec, plan, routing)
    try:
        raw = invoker.invoke(
            role="chief_architect",
            prompt=prompt,
            json_schema=DecompositionResult.model_json_schema(),
        )
    except Exception as e:
        # TODO: DBOS step retry will handle transient failures in Phase 3B.
        raise RuntimeError(f"CA decomposition invocation failed: {e}") from e
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
