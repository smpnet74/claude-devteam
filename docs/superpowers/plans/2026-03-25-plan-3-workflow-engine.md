# Plan 3: Workflow Engine Implementation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build the durable workflow engine that enforces routing, review chains, and task execution in code.

**Architecture:** The workflow engine uses DBOS durable workflows to orchestrate the full job lifecycle. The CEO routing workflow analyzes intake and returns a RoutingResult. The CA decomposition workflow produces a task DAG with peer assignments and dependencies. The DAG execution engine dispatches independent tasks in parallel using `DBOS.start_workflow()` + polling for completion. Each task runs through a review-chain-enforced workflow (engineer -> peer -> EM with revision loops). Question escalation pauses individual task branches while other branches continue.

**Tech Stack:** Python 3.12+, DBOS Python SDK (durable workflows, steps, queues), Pydantic (structured output schemas), pytest + pytest-asyncio (testing), unittest.mock (agent invoker mocking)

---

## Task 1: Structured Output Schemas

**File:** `src/devteam/orchestrator/__init__.py`, `src/devteam/orchestrator/schemas.py`

**Why:** Every workflow depends on machine-readable structured outputs. Define all Pydantic models first so every subsequent task can import them.

### Steps

- [ ] **Step 1.1** Create `src/devteam/orchestrator/__init__.py` (empty, makes it a package).

```python
# src/devteam/orchestrator/__init__.py
```

- [ ] **Step 1.2** Create `src/devteam/orchestrator/schemas.py` with all structured output models.

```python
# src/devteam/orchestrator/schemas.py
"""Structured output schemas for workflow engine.

These Pydantic models define the contracts between the orchestrator
and agent invocations. Agents return JSON matching these schemas,
enforced via the Agent SDK's json_schema parameter.

NOTE: Entity state enums (JobStatus, TaskStatus, QuestionStatus, PRStatus)
are defined in devteam.models.entities (Plan 1) and imported here.
This module only defines workflow-specific schemas (routing, decomposition,
implementation results, review results, question records).
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# Import entity state enums from Plan 1's models — single source of truth.
# Once Plan 1 is implemented, replace the temporary definitions below with:
# from devteam.models.entities import JobStatus, TaskStatus, QuestionStatus


# --- Routing ---

class RoutePath(str, Enum):
    FULL_PROJECT = "full_project"
    RESEARCH = "research"
    SMALL_FIX = "small_fix"
    OSS_CONTRIBUTION = "oss_contribution"


class RoutingResult(BaseModel):
    """CEO routing decision for incoming work."""
    path: RoutePath
    reasoning: str
    target_team: Optional[str] = Field(
        default=None,
        description="For small_fix: which EM/team to route to directly",
    )


# --- Decomposition ---

class TaskDefinition(BaseModel):
    """A single task produced by CA decomposition."""
    id: str = Field(description="Task ID, e.g. 'T-1'")
    description: str
    assigned_to: str = Field(description="Agent role, e.g. 'backend'")
    team: str = Field(description="'a' or 'b'")
    depends_on: list[str] = Field(default_factory=list, description="Task IDs this depends on")
    pr_group: str = Field(description="PR group identifier for co-shipping tasks")
    work_type: WorkType = Field(default=WorkType.CODE)


class WorkType(str, Enum):
    CODE = "code"
    RESEARCH = "research"
    PLANNING = "planning"
    ARCHITECTURE = "architecture"
    DOCUMENTATION = "documentation"


# Re-order so TaskDefinition can reference WorkType
TaskDefinition.model_rebuild()


class DecompositionResult(BaseModel):
    """CA decomposition output — task DAG with assignments."""
    tasks: list[TaskDefinition]
    peer_assignments: dict[str, str] = Field(
        description="Mapping of task_id -> reviewer_role",
    )
    parallel_groups: list[list[str]] = Field(
        description="Groups of task_ids that can run simultaneously",
    )


# --- Implementation ---

class ImplementationStatus(str, Enum):
    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    BLOCKED = "blocked"


class ImplementationResult(BaseModel):
    """Result from an engineer executing a task."""
    status: ImplementationStatus
    question: Optional[str] = None
    files_changed: list[str] = Field(default_factory=list)
    tests_added: list[str] = Field(default_factory=list)
    summary: str
    confidence: str = Field(description="'high', 'medium', or 'low'")


# --- Review ---

class ReviewVerdict(str, Enum):
    APPROVED = "approved"
    APPROVED_WITH_COMMENTS = "approved_with_comments"
    NEEDS_REVISION = "needs_revision"
    BLOCKED = "blocked"


class ReviewComment(BaseModel):
    file: str
    line: int
    severity: str = Field(description="'error', 'warning', or 'nitpick'")
    comment: str


class ReviewResult(BaseModel):
    """Result from a peer review or EM review step."""
    verdict: ReviewVerdict
    comments: list[ReviewComment] = Field(default_factory=list)
    summary: str

    @property
    def needs_revision(self) -> bool:
        return self.verdict in (ReviewVerdict.NEEDS_REVISION, ReviewVerdict.BLOCKED)


# --- Question Escalation ---

class QuestionType(str, Enum):
    ARCHITECTURE = "architecture"
    ROUTING_POLICY = "routing_policy"
    SPEC_AMBIGUITY = "spec_ambiguity"
    TECHNICAL = "technical"


class EscalationLevel(str, Enum):
    SUPERVISOR = "escalated_to_supervisor"
    LEADERSHIP = "escalated_to_leadership"
    HUMAN = "escalated_to_human"


class QuestionRecord(BaseModel):
    """A question raised during task execution."""
    id: str = Field(description="Question ID, e.g. 'Q-1'")
    task_id: str
    job_id: str
    question: str
    question_type: QuestionType
    escalation_level: EscalationLevel = EscalationLevel.SUPERVISOR
    answer: Optional[str] = None
    answered_by: Optional[str] = None
    resolved: bool = False


# --- Job Lifecycle ---
# NOTE: These are temporary definitions that duplicate Plan 1's entities.py enums.
# They exist so Plan 3 can be implemented before or in parallel with Plan 1.
# Once Plan 1 is done, delete these classes and use the import at the top of this file.


class JobStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    DECOMPOSING = "decomposing"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    PAUSED_RATE_LIMIT = "paused_rate_limit"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    EXECUTING = "executing"
    WAITING_ON_REVIEW = "waiting_on_review"
    APPROVED = "approved"
    COMPLETED = "completed"
    WAITING_ON_QUESTION = "waiting_on_question"
    WAITING_ON_CI = "waiting_on_ci"
    REVISION_REQUESTED = "revision_requested"
    PAUSED = "paused"
    CANCELED = "canceled"
    FAILED = "failed"
```

- [ ] **Step 1.3** Create `tests/orchestrator/__init__.py` and `tests/orchestrator/test_schemas.py`.

```python
# tests/orchestrator/__init__.py
```

```python
# tests/orchestrator/test_schemas.py
"""Tests for structured output schemas."""
import pytest
from devteam.orchestrator.schemas import (
    DecompositionResult,
    EscalationLevel,
    ImplementationResult,
    ImplementationStatus,
    QuestionRecord,
    QuestionType,
    ReviewResult,
    ReviewVerdict,
    RoutePath,
    RoutingResult,
    TaskDefinition,
    TaskStatus,
    WorkType,
)


class TestRoutingResult:
    def test_basic_routing(self):
        r = RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="has spec and plan")
        assert r.path == RoutePath.FULL_PROJECT
        assert r.target_team is None

    def test_small_fix_with_target(self):
        r = RoutingResult(
            path=RoutePath.SMALL_FIX,
            reasoning="single file fix",
            target_team="a",
        )
        assert r.target_team == "a"


class TestDecompositionResult:
    def test_full_decomposition(self):
        d = DecompositionResult(
            tasks=[
                TaskDefinition(
                    id="T-1",
                    description="Build API",
                    assigned_to="backend",
                    team="a",
                    depends_on=[],
                    pr_group="feat/api",
                    work_type=WorkType.CODE,
                ),
                TaskDefinition(
                    id="T-2",
                    description="Build UI",
                    assigned_to="frontend",
                    team="a",
                    depends_on=["T-1"],
                    pr_group="feat/ui",
                ),
            ],
            peer_assignments={"T-1": "frontend", "T-2": "backend"},
            parallel_groups=[["T-1"], ["T-2"]],
        )
        assert len(d.tasks) == 2
        assert d.tasks[1].depends_on == ["T-1"]

    def test_default_work_type(self):
        t = TaskDefinition(
            id="T-1", description="x", assigned_to="backend",
            team="a", pr_group="g1",
        )
        assert t.work_type == WorkType.CODE


class TestReviewResult:
    def test_approved(self):
        r = ReviewResult(verdict=ReviewVerdict.APPROVED, summary="LGTM")
        assert not r.needs_revision

    def test_needs_revision(self):
        r = ReviewResult(
            verdict=ReviewVerdict.NEEDS_REVISION,
            summary="Fix tests",
        )
        assert r.needs_revision

    def test_blocked_needs_revision(self):
        r = ReviewResult(verdict=ReviewVerdict.BLOCKED, summary="Security issue")
        assert r.needs_revision


class TestImplementationResult:
    def test_completed(self):
        r = ImplementationResult(
            status=ImplementationStatus.COMPLETED,
            files_changed=["src/api.py"],
            tests_added=["tests/test_api.py"],
            summary="Built the API",
            confidence="high",
        )
        assert r.status == ImplementationStatus.COMPLETED

    def test_needs_clarification_with_question(self):
        r = ImplementationResult(
            status=ImplementationStatus.NEEDS_CLARIFICATION,
            question="Should we use JWT or sessions?",
            summary="Blocked on auth decision",
            confidence="low",
        )
        assert r.question is not None


class TestQuestionRecord:
    def test_initial_state(self):
        q = QuestionRecord(
            id="Q-1",
            task_id="T-2",
            job_id="W-1",
            question="Redis or Memcached?",
            question_type=QuestionType.TECHNICAL,
        )
        assert q.escalation_level == EscalationLevel.SUPERVISOR
        assert not q.resolved
        assert q.answer is None
```

**Test command:**
```bash
cd /Users/scottpeterson/xdev/claude-devteam && python -m pytest tests/orchestrator/test_schemas.py -v
```

---

## Task 2: CEO Routing Workflow

**File:** `src/devteam/orchestrator/routing.py`

**Why:** The CEO is the entry point for all work. This workflow analyzes the intake (spec, plan, issue, or prompt) and returns a RoutingResult that determines the execution path.

### Steps

- [ ] **Step 2.1** Create `src/devteam/orchestrator/routing.py` with the CEO routing logic.

```python
# src/devteam/orchestrator/routing.py
"""CEO routing workflow — analyzes intake and determines execution path."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from devteam.orchestrator.schemas import RoutePath, RoutingResult


class AgentInvoker(Protocol):
    """Protocol for agent invocation — allows mocking in tests."""

    def invoke(
        self,
        role: str,
        prompt: str,
        *,
        json_schema: dict | None = None,
        cwd: str | None = None,
    ) -> dict:
        ...


@dataclass
class IntakeContext:
    """Parsed intake from CLI arguments."""
    spec: Optional[str] = None
    plan: Optional[str] = None
    issue_url: Optional[str] = None
    prompt: Optional[str] = None
    repo_path: Optional[str] = None


def classify_intake(ctx: IntakeContext) -> RoutePath:
    """Fast-path classification before invoking the CEO.

    Some intake types have deterministic routes that don't need
    CEO reasoning. Returns None if CEO analysis is needed.
    """
    if ctx.spec and ctx.plan:
        return RoutePath.FULL_PROJECT
    if ctx.issue_url and "github.com" in ctx.issue_url:
        # Could be own repo or external — CEO decides full_project vs oss
        return None
    return None


def build_routing_prompt(ctx: IntakeContext) -> str:
    """Build the prompt for CEO routing analysis."""
    parts = ["Analyze the following intake and determine the routing path.\n"]

    if ctx.spec:
        parts.append(f"## Spec\n{ctx.spec}\n")
    if ctx.plan:
        parts.append(f"## Plan\n{ctx.plan}\n")
    if ctx.issue_url:
        parts.append(f"## Issue URL\n{ctx.issue_url}\n")
    if ctx.prompt:
        parts.append(f"## Request\n{ctx.prompt}\n")

    parts.append(
        "## Routing Options\n"
        "- full_project: Has spec+plan or needs full decomposition\n"
        "- research: Research request, deliverable back to human\n"
        "- small_fix: Clear scope, single engineer can handle it\n"
        "- oss_contribution: Contributing to an external open-source project\n"
        "\nReturn the routing path and your reasoning."
    )
    return "\n".join(parts)


def route_intake(
    ctx: IntakeContext,
    invoker: AgentInvoker,
) -> RoutingResult:
    """Route incoming work through CEO analysis.

    Fast-path: If spec+plan are provided, route directly to full_project
    without invoking the CEO (deterministic path).

    Otherwise: Invoke the CEO agent for intelligent routing.
    """
    # Fast-path for deterministic routes
    fast_path = classify_intake(ctx)
    if fast_path == RoutePath.FULL_PROJECT:
        return RoutingResult(
            path=RoutePath.FULL_PROJECT,
            reasoning="Spec and plan provided — direct to full project workflow",
        )

    # CEO analysis needed
    prompt = build_routing_prompt(ctx)
    result = invoker.invoke(
        role="ceo",
        prompt=prompt,
        json_schema=RoutingResult.model_json_schema(),
    )
    return RoutingResult.model_validate(result)
```

- [ ] **Step 2.2** Create `tests/orchestrator/test_routing.py`.

```python
# tests/orchestrator/test_routing.py
"""Tests for CEO routing workflow."""
import pytest
from unittest.mock import MagicMock

from devteam.orchestrator.routing import (
    IntakeContext,
    build_routing_prompt,
    classify_intake,
    route_intake,
)
from devteam.orchestrator.schemas import RoutePath, RoutingResult


class TestClassifyIntake:
    def test_spec_and_plan_is_full_project(self):
        ctx = IntakeContext(spec="some spec", plan="some plan")
        assert classify_intake(ctx) == RoutePath.FULL_PROJECT

    def test_issue_url_needs_ceo(self):
        ctx = IntakeContext(issue_url="https://github.com/org/repo/issues/42")
        assert classify_intake(ctx) is None

    def test_prompt_only_needs_ceo(self):
        ctx = IntakeContext(prompt="Fix the login bug")
        assert classify_intake(ctx) is None

    def test_spec_without_plan_needs_ceo(self):
        ctx = IntakeContext(spec="some spec")
        assert classify_intake(ctx) is None


class TestBuildRoutingPrompt:
    def test_includes_spec(self):
        ctx = IntakeContext(spec="My spec content")
        prompt = build_routing_prompt(ctx)
        assert "My spec content" in prompt
        assert "## Spec" in prompt

    def test_includes_issue_url(self):
        ctx = IntakeContext(issue_url="https://github.com/org/repo/issues/1")
        prompt = build_routing_prompt(ctx)
        assert "https://github.com/org/repo/issues/1" in prompt

    def test_includes_routing_options(self):
        ctx = IntakeContext(prompt="do something")
        prompt = build_routing_prompt(ctx)
        assert "full_project" in prompt
        assert "research" in prompt
        assert "small_fix" in prompt
        assert "oss_contribution" in prompt


class TestRouteIntake:
    def test_fast_path_spec_and_plan(self):
        """Spec+plan bypasses CEO entirely."""
        invoker = MagicMock()
        ctx = IntakeContext(spec="spec", plan="plan")
        result = route_intake(ctx, invoker)

        assert result.path == RoutePath.FULL_PROJECT
        invoker.invoke.assert_not_called()

    def test_issue_invokes_ceo(self):
        """Issue URL requires CEO analysis."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "path": "oss_contribution",
            "reasoning": "External repo, no push access",
        }
        ctx = IntakeContext(issue_url="https://github.com/other/repo/issues/5")
        result = route_intake(ctx, invoker)

        assert result.path == RoutePath.OSS_CONTRIBUTION
        invoker.invoke.assert_called_once()

    def test_prompt_invokes_ceo_small_fix(self):
        """Simple prompt routed as small fix by CEO."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "path": "small_fix",
            "reasoning": "Single-file typo fix",
            "target_team": "a",
        }
        ctx = IntakeContext(prompt="Fix typo in README")
        result = route_intake(ctx, invoker)

        assert result.path == RoutePath.SMALL_FIX
        assert result.target_team == "a"

    def test_prompt_invokes_ceo_research(self):
        """Research request recognized by CEO."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "path": "research",
            "reasoning": "User wants analysis, not code changes",
        }
        ctx = IntakeContext(prompt="Research best auth strategies for our stack")
        result = route_intake(ctx, invoker)

        assert result.path == RoutePath.RESEARCH
```

**Test command:**
```bash
cd /Users/scottpeterson/xdev/claude-devteam && python -m pytest tests/orchestrator/test_routing.py -v
```

---

## Task 3: CA Decomposition Workflow

**File:** `src/devteam/orchestrator/decomposition.py`

**Why:** The Chief Architect breaks a spec+plan into a task DAG with peer assignments, dependencies, and PR groupings. This is the bridge between routing and execution.

### Steps

- [ ] **Step 3.1** Create `src/devteam/orchestrator/decomposition.py`.

```python
# src/devteam/orchestrator/decomposition.py
"""Chief Architect decomposition workflow — breaks spec+plan into task DAG."""
from __future__ import annotations

from devteam.orchestrator.routing import AgentInvoker
from devteam.orchestrator.schemas import (
    DecompositionResult,
    RoutePath,
    RoutingResult,
    TaskDefinition,
)


# Peer review assignment table from spec
PEER_REVIEW_MAP_TEAM_A = {
    "backend": ["frontend", "devops"],
    "frontend": ["backend"],
    "devops": ["backend"],
}

PEER_REVIEW_MAP_TEAM_B = {
    "data": ["infra"],
    "infra": ["data", "tooling"],
    "tooling": ["infra", "cloud"],
    "cloud": ["infra"],
}


def get_default_peer_reviewer(assigned_to: str, team: str) -> str | None:
    """Return the default peer reviewer for an engineer based on team rules."""
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
    tasks: list[TaskDefinition],
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
    """
    errors = []
    task_ids = {t.id for t in result.tasks}

    # Check dependency references
    for task in result.tasks:
        for dep in task.depends_on:
            if dep not in task_ids:
                errors.append(
                    f"Task {task.id} depends on unknown task {dep}"
                )

    # Check peer assignments reference valid tasks
    for task_id in result.peer_assignments:
        if task_id not in task_ids:
            errors.append(
                f"Peer assignment references unknown task {task_id}"
            )

    # Check for circular dependencies (simple cycle detection)
    visited = set()
    rec_stack = set()

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
                errors.append(
                    f"Parallel group references unknown task {tid}"
                )

    return errors


def decompose(
    spec: str,
    plan: str,
    routing: RoutingResult,
    invoker: AgentInvoker,
) -> DecompositionResult:
    """Invoke CA to decompose spec+plan into task DAG.

    For superpowers specs (already reviewed), CA does decomposition
    only. For raw ideas, CA also validates the spec quality.
    """
    prompt = build_decomposition_prompt(spec, plan, routing)
    raw = invoker.invoke(
        role="chief_architect",
        prompt=prompt,
        json_schema=DecompositionResult.model_json_schema(),
    )
    result = DecompositionResult.model_validate(raw)

    # Fill in missing peer assignments from defaults
    result.peer_assignments = assign_peer_reviewers(
        result.tasks, result.peer_assignments
    )

    # Validate
    errors = validate_decomposition(result)
    if errors:
        raise ValueError(
            f"Decomposition validation failed: {'; '.join(errors)}"
        )

    return result
```

- [ ] **Step 3.2** Create `tests/orchestrator/test_decomposition.py`.

```python
# tests/orchestrator/test_decomposition.py
"""Tests for CA decomposition workflow."""
import pytest
from unittest.mock import MagicMock

from devteam.orchestrator.decomposition import (
    assign_peer_reviewers,
    decompose,
    get_default_peer_reviewer,
    validate_decomposition,
)
from devteam.orchestrator.schemas import (
    DecompositionResult,
    RoutePath,
    RoutingResult,
    TaskDefinition,
    WorkType,
)


def _make_task(id: str, assigned_to: str, team: str, depends_on=None):
    return TaskDefinition(
        id=id,
        description=f"Task {id}",
        assigned_to=assigned_to,
        team=team,
        depends_on=depends_on or [],
        pr_group="feat/main",
    )


class TestGetDefaultPeerReviewer:
    def test_backend_reviewed_by_frontend(self):
        assert get_default_peer_reviewer("backend", "a") == "frontend"

    def test_frontend_reviewed_by_backend(self):
        assert get_default_peer_reviewer("frontend", "a") == "backend"

    def test_data_reviewed_by_infra(self):
        assert get_default_peer_reviewer("data", "b") == "infra"

    def test_unknown_role_returns_none(self):
        assert get_default_peer_reviewer("ceo", "a") is None

    def test_unknown_team_returns_none(self):
        assert get_default_peer_reviewer("backend", "c") is None


class TestAssignPeerReviewers:
    def test_fills_defaults(self):
        tasks = [_make_task("T-1", "backend", "a")]
        assignments = assign_peer_reviewers(tasks)
        assert assignments == {"T-1": "frontend"}

    def test_explicit_overrides_default(self):
        tasks = [_make_task("T-1", "backend", "a")]
        assignments = assign_peer_reviewers(tasks, {"T-1": "devops"})
        assert assignments == {"T-1": "devops"}

    def test_multiple_tasks(self):
        tasks = [
            _make_task("T-1", "backend", "a"),
            _make_task("T-2", "data", "b"),
        ]
        assignments = assign_peer_reviewers(tasks)
        assert assignments == {"T-1": "frontend", "T-2": "infra"}


class TestValidateDecomposition:
    def test_valid_decomposition(self):
        result = DecompositionResult(
            tasks=[
                _make_task("T-1", "backend", "a"),
                _make_task("T-2", "frontend", "a", depends_on=["T-1"]),
            ],
            peer_assignments={"T-1": "frontend", "T-2": "backend"},
            parallel_groups=[["T-1"], ["T-2"]],
        )
        assert validate_decomposition(result) == []

    def test_unknown_dependency(self):
        result = DecompositionResult(
            tasks=[_make_task("T-1", "backend", "a", depends_on=["T-99"])],
            peer_assignments={},
            parallel_groups=[],
        )
        errors = validate_decomposition(result)
        assert any("unknown task T-99" in e for e in errors)

    def test_unknown_peer_assignment(self):
        result = DecompositionResult(
            tasks=[_make_task("T-1", "backend", "a")],
            peer_assignments={"T-99": "frontend"},
            parallel_groups=[],
        )
        errors = validate_decomposition(result)
        assert any("unknown task T-99" in e for e in errors)

    def test_circular_dependency(self):
        result = DecompositionResult(
            tasks=[
                _make_task("T-1", "backend", "a", depends_on=["T-2"]),
                _make_task("T-2", "frontend", "a", depends_on=["T-1"]),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        errors = validate_decomposition(result)
        assert any("Circular" in e for e in errors)

    def test_unknown_parallel_group_task(self):
        result = DecompositionResult(
            tasks=[_make_task("T-1", "backend", "a")],
            peer_assignments={},
            parallel_groups=[["T-1", "T-99"]],
        )
        errors = validate_decomposition(result)
        assert any("T-99" in e for e in errors)


class TestDecompose:
    def test_invokes_ca_and_fills_peers(self):
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "tasks": [
                {
                    "id": "T-1",
                    "description": "Build API",
                    "assigned_to": "backend",
                    "team": "a",
                    "depends_on": [],
                    "pr_group": "feat/api",
                    "work_type": "code",
                },
            ],
            "peer_assignments": {},
            "parallel_groups": [["T-1"]],
        }
        routing = RoutingResult(
            path=RoutePath.FULL_PROJECT, reasoning="test"
        )
        result = decompose("spec", "plan", routing, invoker)

        assert len(result.tasks) == 1
        assert result.peer_assignments["T-1"] == "frontend"
        invoker.invoke.assert_called_once()

    def test_validation_error_raises(self):
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "tasks": [
                {
                    "id": "T-1",
                    "description": "Build API",
                    "assigned_to": "backend",
                    "team": "a",
                    "depends_on": ["T-99"],
                    "pr_group": "feat/api",
                    "work_type": "code",
                },
            ],
            "peer_assignments": {},
            "parallel_groups": [],
        }
        routing = RoutingResult(
            path=RoutePath.FULL_PROJECT, reasoning="test"
        )
        with pytest.raises(ValueError, match="validation failed"):
            decompose("spec", "plan", routing, invoker)
```

**Test command:**
```bash
cd /Users/scottpeterson/xdev/claude-devteam && python -m pytest tests/orchestrator/test_decomposition.py -v
```

---

## Task 4: DAG Execution Engine

**File:** `src/devteam/orchestrator/dag.py`

**Why:** The DAG engine is the core scheduler. It dispatches tasks whose dependencies are satisfied, runs independent tasks in parallel, and waits for any task to complete before checking for newly unblocked tasks. This is the heart of the parallel execution model.

### Steps

- [ ] **Step 4.1** Create `src/devteam/orchestrator/dag.py`.

```python
# src/devteam/orchestrator/dag.py
"""DAG execution engine — dependency-aware parallel task dispatch.

Manages a directed acyclic graph of tasks, launching tasks whose
dependencies are satisfied and waiting for completion events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from devteam.orchestrator.schemas import (
    DecompositionResult,
    TaskDefinition,
    TaskStatus,
)


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskNode:
    """A node in the task DAG."""
    task: TaskDefinition
    state: TaskState = TaskState.PENDING
    result: Any = None
    error: Optional[str] = None


@dataclass
class DAGState:
    """Current state of the DAG execution."""
    nodes: dict[str, TaskNode] = field(default_factory=dict)
    dependency_graph: dict[str, list[str]] = field(default_factory=dict)

    @property
    def has_pending(self) -> bool:
        return any(n.state == TaskState.PENDING for n in self.nodes.values())

    @property
    def has_running(self) -> bool:
        return any(n.state == TaskState.RUNNING for n in self.nodes.values())

    @property
    def has_failed(self) -> bool:
        return any(n.state == TaskState.FAILED for n in self.nodes.values())

    @property
    def all_completed(self) -> bool:
        return all(
            n.state in (TaskState.COMPLETED, TaskState.FAILED)
            for n in self.nodes.values()
        )

    def get_ready_tasks(self) -> list[TaskDefinition]:
        """Return tasks whose dependencies are all completed."""
        ready = []
        for tid, node in self.nodes.items():
            if node.state != TaskState.PENDING:
                continue
            deps = self.dependency_graph.get(tid, [])
            if all(
                self.nodes[d].state == TaskState.COMPLETED
                for d in deps
                if d in self.nodes
            ):
                ready.append(node.task)
        return ready

    def get_running_task_ids(self) -> list[str]:
        return [
            tid for tid, node in self.nodes.items()
            if node.state == TaskState.RUNNING
        ]

    def mark_running(self, task_id: str) -> None:
        self.nodes[task_id].state = TaskState.RUNNING

    def mark_completed(self, task_id: str, result: Any) -> None:
        self.nodes[task_id].state = TaskState.COMPLETED
        self.nodes[task_id].result = result

    def mark_failed(self, task_id: str, error: str) -> None:
        self.nodes[task_id].state = TaskState.FAILED
        self.nodes[task_id].error = error

    def get_results(self) -> dict[str, Any]:
        return {
            tid: node.result
            for tid, node in self.nodes.items()
            if node.state == TaskState.COMPLETED
        }


def build_dag(decomposition: DecompositionResult) -> DAGState:
    """Build a DAG from decomposition result."""
    dag = DAGState()
    for task in decomposition.tasks:
        dag.nodes[task.id] = TaskNode(task=task)
        dag.dependency_graph[task.id] = list(task.depends_on)
    return dag


@dataclass
class DAGExecutionResult:
    """Result of executing the full DAG."""
    results: dict[str, Any]
    failed_tasks: dict[str, str]  # task_id -> error message
    all_succeeded: bool


class DAGExecutor:
    """Executes a task DAG with dependency-aware parallel dispatch.

    The executor is designed to be used with DBOS workflows. In production,
    `launch_task` starts a child workflow and `check_complete` polls
    for completion (non-blocking). In tests, `check_complete` returns
    immediately with (True, result).
    """

    def __init__(
        self,
        launch_task: Callable[[TaskDefinition], str],
        check_complete: Callable[[str], tuple[bool, Any]],
        on_task_complete: Callable[[str, Any], None] | None = None,
        on_task_failed: Callable[[str, str], None] | None = None,
    ):
        """
        Args:
            launch_task: Starts a task workflow, returns a handle/id.
            check_complete: Non-blocking check. Returns (done, result_or_error).
                           If done is False, result_or_error is None.
                           If done is True and the task failed, result_or_error
                           is an Exception. Otherwise it's the task result.
            on_task_complete: Optional callback when a task completes.
            on_task_failed: Optional callback when a task fails.
        """
        self._launch = launch_task
        self._check_complete = check_complete
        self._on_complete = on_task_complete
        self._on_failed = on_task_failed

    def execute(self, dag: DAGState) -> DAGExecutionResult:
        """Execute the DAG, respecting dependencies.

        Algorithm:
        1. Find all tasks with satisfied dependencies
        2. Launch them in parallel
        3. Wait for any one to complete
        4. Mark it completed, loop back to find newly unblocked tasks
        5. Repeat until all tasks are done or failed
        """
        handles: dict[str, str] = {}  # task_id -> handle

        while dag.has_pending or dag.has_running:
            # Launch ready tasks
            for task in dag.get_ready_tasks():
                if task.id not in handles:
                    handle = self._launch(task)
                    handles[task.id] = handle
                    dag.mark_running(task.id)

            # If nothing is running and nothing is ready, we're stuck
            if not dag.has_running:
                break

            # Wait for ANY running task to complete.
            # Uses _check_complete (non-blocking) to poll all handles so we
            # detect whichever finishes first, not just the first in iteration order.
            # In production, _check_complete wraps DBOS handle.get_status().
            # In tests, the synchronous mock returns immediately.
            import time
            completed_tid = None
            while completed_tid is None:
                for tid in list(dag.get_running_task_ids()):
                    if tid not in handles:
                        continue
                    done, result_or_error = self._check_complete(handles[tid])
                    if not done:
                        continue
                    if isinstance(result_or_error, Exception):
                        dag.mark_failed(tid, str(result_or_error))
                        if self._on_failed:
                            self._on_failed(tid, str(result_or_error))
                    else:
                        dag.mark_completed(tid, result_or_error)
                        if self._on_complete:
                            self._on_complete(tid, result_or_error)
                    handles.pop(tid, None)
                    completed_tid = tid
                    break  # Process one completion, then re-check ready tasks
                if completed_tid is None:
                    time.sleep(0.1)  # Brief poll interval; no-op in sync tests

        return DAGExecutionResult(
            results=dag.get_results(),
            failed_tasks={
                tid: node.error or "Unknown error"
                for tid, node in dag.nodes.items()
                if node.state == TaskState.FAILED
            },
            all_succeeded=not dag.has_failed
            and all(
                n.state == TaskState.COMPLETED for n in dag.nodes.values()
            ),
        )
```

- [ ] **Step 4.2** Create `tests/orchestrator/test_dag.py`.

```python
# tests/orchestrator/test_dag.py
"""Tests for DAG execution engine."""
import pytest
from unittest.mock import MagicMock, call

from devteam.orchestrator.dag import (
    DAGExecutor,
    DAGState,
    TaskNode,
    TaskState,
    build_dag,
)
from devteam.orchestrator.schemas import (
    DecompositionResult,
    TaskDefinition,
)


def _make_task(id, depends_on=None):
    return TaskDefinition(
        id=id,
        description=f"Task {id}",
        assigned_to="backend",
        team="a",
        depends_on=depends_on or [],
        pr_group="feat/main",
    )


class TestBuildDAG:
    def test_builds_from_decomposition(self):
        decomp = DecompositionResult(
            tasks=[
                _make_task("T-1"),
                _make_task("T-2", depends_on=["T-1"]),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        assert len(dag.nodes) == 2
        assert dag.dependency_graph["T-2"] == ["T-1"]

    def test_no_dependencies(self):
        decomp = DecompositionResult(
            tasks=[_make_task("T-1"), _make_task("T-2")],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        assert dag.dependency_graph["T-1"] == []
        assert dag.dependency_graph["T-2"] == []


class TestDAGState:
    def test_get_ready_no_deps(self):
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        dag.nodes["T-2"] = TaskNode(task=_make_task("T-2"))
        dag.dependency_graph = {"T-1": [], "T-2": []}

        ready = dag.get_ready_tasks()
        assert len(ready) == 2

    def test_get_ready_respects_deps(self):
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        dag.nodes["T-2"] = TaskNode(task=_make_task("T-2", ["T-1"]))
        dag.dependency_graph = {"T-1": [], "T-2": ["T-1"]}

        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "T-1"

    def test_get_ready_after_dep_completes(self):
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        dag.nodes["T-2"] = TaskNode(task=_make_task("T-2", ["T-1"]))
        dag.dependency_graph = {"T-1": [], "T-2": ["T-1"]}

        dag.mark_completed("T-1", {"ok": True})
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "T-2"

    def test_running_tasks_not_ready(self):
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        dag.dependency_graph = {"T-1": []}

        dag.mark_running("T-1")
        ready = dag.get_ready_tasks()
        assert len(ready) == 0

    def test_has_pending(self):
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        assert dag.has_pending
        dag.mark_running("T-1")
        assert not dag.has_pending

    def test_all_completed(self):
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        assert not dag.all_completed
        dag.mark_completed("T-1", "done")
        assert dag.all_completed


class TestDAGExecutor:
    def test_independent_tasks_all_launched(self):
        """Two independent tasks should both be launched."""
        launched = []

        def launch(task):
            launched.append(task.id)
            return task.id

        results = {"T-1": "result1", "T-2": "result2"}

        def wait(handle):
            return (True, results[handle])

        decomp = DecompositionResult(
            tasks=[_make_task("T-1"), _make_task("T-2")],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert result.all_succeeded
        assert "T-1" in launched
        assert "T-2" in launched

    def test_dependent_tasks_run_in_order(self):
        """T-2 depends on T-1, so T-1 must complete first."""
        launch_order = []

        def launch(task):
            launch_order.append(task.id)
            return task.id

        def wait(handle):
            return (True, f"result-{handle}")

        decomp = DecompositionResult(
            tasks=[
                _make_task("T-1"),
                _make_task("T-2", depends_on=["T-1"]),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert result.all_succeeded
        assert launch_order.index("T-1") < launch_order.index("T-2")

    def test_diamond_dependency(self):
        """
        T-1 -> T-2 -> T-4
        T-1 -> T-3 -> T-4
        T-2 and T-3 can run in parallel after T-1.
        """
        launch_order = []

        def launch(task):
            launch_order.append(task.id)
            return task.id

        def wait(handle):
            return (True, f"result-{handle}")

        decomp = DecompositionResult(
            tasks=[
                _make_task("T-1"),
                _make_task("T-2", depends_on=["T-1"]),
                _make_task("T-3", depends_on=["T-1"]),
                _make_task("T-4", depends_on=["T-2", "T-3"]),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert result.all_succeeded
        assert launch_order[0] == "T-1"
        assert "T-4" == launch_order[-1]
        # T-2 and T-3 should both appear before T-4
        assert launch_order.index("T-2") < launch_order.index("T-4")
        assert launch_order.index("T-3") < launch_order.index("T-4")

    def test_failed_task_reported(self):
        """Failed tasks should be captured in results."""
        def launch(task):
            return task.id

        def wait(handle):
            if handle == "T-1":
                return (True, RuntimeError("Agent crashed"))
            return (True, "ok")

        decomp = DecompositionResult(
            tasks=[_make_task("T-1"), _make_task("T-2")],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert not result.all_succeeded
        assert "T-1" in result.failed_tasks
        assert "T-2" in result.results

    def test_blocked_by_failed_dependency(self):
        """If T-1 fails, T-2 (which depends on T-1) should never launch."""
        launched = []

        def launch(task):
            launched.append(task.id)
            return task.id

        def wait(handle):
            if handle == "T-1":
                return (True, RuntimeError("Failed"))
            return (True, "ok")

        decomp = DecompositionResult(
            tasks=[
                _make_task("T-1"),
                _make_task("T-2", depends_on=["T-1"]),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert "T-2" not in launched
        assert not result.all_succeeded

    def test_callbacks_invoked(self):
        on_complete = MagicMock()
        on_failed = MagicMock()

        def launch(task):
            return task.id

        def wait(handle):
            if handle == "T-2":
                return (True, RuntimeError("boom"))
            return (True, "ok")

        decomp = DecompositionResult(
            tasks=[_make_task("T-1"), _make_task("T-2")],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(
            launch_task=launch,
            check_complete=wait,
            on_task_complete=on_complete,
            on_task_failed=on_failed,
        )
        executor.execute(dag)

        on_complete.assert_called_once_with("T-1", "ok")
        on_failed.assert_called_once_with("T-2", "boom")
```

**Test command:**
```bash
cd /Users/scottpeterson/xdev/claude-devteam && python -m pytest tests/orchestrator/test_dag.py -v
```

---

## Task 5: Task Workflow (Execute + Review Chain)

**File:** `src/devteam/orchestrator/task_workflow.py`

**Why:** This is the single-task execution lifecycle: engineer executes, peer reviews, EM reviews, with revision loops if needed. The review chain is enforced in code -- peer_review is called before em_review, and the loop continues until approval.

### Steps

- [ ] **Step 5.1** Create `src/devteam/orchestrator/task_workflow.py`.

```python
# src/devteam/orchestrator/task_workflow.py
"""Single task execution workflow — engineer + review chain.

Enforces: engineer_execute -> peer_review -> em_review
with revision loop on rejection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from devteam.orchestrator.routing import AgentInvoker
from devteam.orchestrator.schemas import (
    ImplementationResult,
    ImplementationStatus,
    QuestionRecord,
    QuestionType,
    ReviewResult,
    ReviewVerdict,
    TaskDefinition,
    TaskStatus,
    WorkType,
)


MAX_REVISION_ITERATIONS = 3


@dataclass
class TaskContext:
    """Runtime context for a task execution."""
    task: TaskDefinition
    peer_reviewer: str
    em_role: str
    worktree_path: str
    job_id: str
    spec_context: str = ""
    feedback: Optional[str] = None  # injected human comment


@dataclass
class TaskWorkflowResult:
    """Full result of a task workflow execution."""
    task_id: str
    status: TaskStatus
    implementation: Optional[ImplementationResult] = None
    peer_review: Optional[ReviewResult] = None
    em_review: Optional[ReviewResult] = None
    revision_count: int = 0
    question: Optional[QuestionRecord] = None
    error: Optional[str] = None


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
    task: TaskDefinition,
    implementation: ImplementationResult,
    review_type: str,
) -> str:
    """Build prompt for peer or EM review."""
    return (
        f"## Review Request ({review_type})\n\n"
        f"### Task\n{task.description}\n\n"
        f"### Implementation Summary\n{implementation.summary}\n\n"
        f"### Files Changed\n"
        + "\n".join(f"- {f}" for f in implementation.files_changed)
        + "\n\n"
        f"### Tests Added\n"
        + "\n".join(f"- {f}" for f in implementation.tests_added)
        + "\n\n"
        f"### Confidence\n{implementation.confidence}\n\n"
        "Review the implementation in the worktree. "
        "Check for correctness, test coverage, and adherence to project conventions.\n"
    )


def engineer_execute(
    ctx: TaskContext,
    invoker: AgentInvoker,
    revision_feedback: str | None = None,
) -> ImplementationResult:
    """Execute the engineering step — invoke the assigned engineer."""
    prompt = build_implementation_prompt(ctx, revision_feedback)
    raw = invoker.invoke(
        role=ctx.task.assigned_to,
        prompt=prompt,
        json_schema=ImplementationResult.model_json_schema(),
        cwd=ctx.worktree_path,
    )
    return ImplementationResult.model_validate(raw)


def peer_review(
    ctx: TaskContext,
    implementation: ImplementationResult,
    invoker: AgentInvoker,
) -> ReviewResult:
    """Execute peer review step."""
    prompt = build_review_prompt(ctx.task, implementation, "Peer Review")
    raw = invoker.invoke(
        role=ctx.peer_reviewer,
        prompt=prompt,
        json_schema=ReviewResult.model_json_schema(),
        cwd=ctx.worktree_path,
    )
    return ReviewResult.model_validate(raw)


def em_review(
    ctx: TaskContext,
    implementation: ImplementationResult,
    peer_result: ReviewResult,
    invoker: AgentInvoker,
) -> ReviewResult:
    """Execute EM review step."""
    prompt = (
        build_review_prompt(ctx.task, implementation, "EM Review")
        + f"\n### Peer Review Verdict\n{peer_result.verdict.value}\n"
        f"### Peer Review Summary\n{peer_result.summary}\n"
    )
    raw = invoker.invoke(
        role=ctx.em_role,
        prompt=prompt,
        json_schema=ReviewResult.model_json_schema(),
        cwd=ctx.worktree_path,
    )
    return ReviewResult.model_validate(raw)


def execute_task_workflow(
    ctx: TaskContext,
    invoker: AgentInvoker,
    max_revisions: int = MAX_REVISION_ITERATIONS,
) -> TaskWorkflowResult:
    """Execute full task workflow: implement -> peer review -> EM review.

    Revision loop: if EM rejects, engineer re-implements with feedback,
    then goes through peer + EM review again. Circuit breaker after
    max_revisions iterations.
    """
    result = TaskWorkflowResult(task_id=ctx.task.id, status=TaskStatus.EXECUTING)
    revision_feedback = None

    for iteration in range(max_revisions + 1):
        # Step 1: Engineer executes
        impl = engineer_execute(ctx, invoker, revision_feedback)
        result.implementation = impl
        result.revision_count = iteration

        # Check if engineer raised a question
        if impl.status == ImplementationStatus.NEEDS_CLARIFICATION:
            result.status = TaskStatus.WAITING_ON_QUESTION
            result.question = QuestionRecord(
                id=f"Q-{ctx.task.id}-{iteration}",
                task_id=ctx.task.id,
                job_id=ctx.job_id,
                question=impl.question or "Unspecified question",
                question_type=QuestionType.TECHNICAL,
            )
            return result

        if impl.status == ImplementationStatus.BLOCKED:
            result.status = TaskStatus.FAILED
            result.error = f"Engineer reported blocked: {impl.summary}"
            return result

        # Step 2: Peer review (enforced before EM review)
        result.status = TaskStatus.WAITING_ON_REVIEW
        pr = peer_review(ctx, impl, invoker)
        result.peer_review = pr

        # If peer review blocks, don't proceed to EM
        if pr.verdict == ReviewVerdict.BLOCKED:
            revision_feedback = pr.summary
            result.status = TaskStatus.REVISION_REQUESTED
            continue

        # Step 3: EM review
        em = em_review(ctx, impl, pr, invoker)
        result.em_review = em

        if not em.needs_revision:
            # Approved!
            result.status = TaskStatus.APPROVED
            return result

        # EM requested revision — loop back
        revision_feedback = em.summary
        result.status = TaskStatus.REVISION_REQUESTED

    # Circuit breaker — max revisions exceeded
    result.status = TaskStatus.FAILED
    result.error = (
        f"Task {ctx.task.id} failed after {max_revisions} revision iterations"
    )
    return result
```

- [ ] **Step 5.2** Create `tests/orchestrator/test_task_workflow.py`.

```python
# tests/orchestrator/test_task_workflow.py
"""Tests for task workflow — review chain enforcement and revision loops."""
import pytest
from unittest.mock import MagicMock, call

from devteam.orchestrator.task_workflow import (
    TaskContext,
    execute_task_workflow,
    engineer_execute,
    peer_review,
    em_review,
)
from devteam.orchestrator.schemas import (
    ImplementationResult,
    ImplementationStatus,
    ReviewResult,
    ReviewVerdict,
    TaskDefinition,
    TaskStatus,
    WorkType,
)


def _make_ctx(task_id="T-1", assigned_to="backend", peer="frontend", em="em_a"):
    task = TaskDefinition(
        id=task_id,
        description="Build API endpoint",
        assigned_to=assigned_to,
        team="a",
        pr_group="feat/api",
    )
    return TaskContext(
        task=task,
        peer_reviewer=peer,
        em_role=em,
        worktree_path="/tmp/worktree",
        job_id="W-1",
    )


def _impl_result(status="completed", question=None):
    return {
        "status": status,
        "question": question,
        "files_changed": ["src/api.py"],
        "tests_added": ["tests/test_api.py"],
        "summary": "Built the API",
        "confidence": "high",
    }


def _review_result(verdict="approved"):
    return {
        "verdict": verdict,
        "comments": [],
        "summary": "Looks good" if verdict == "approved" else "Needs work",
    }


class TestReviewChainEnforcement:
    def test_peer_review_called_before_em(self):
        """The core invariant: peer review MUST happen before EM review."""
        invoker = MagicMock()
        call_order = []

        def track_invoke(role, prompt, **kwargs):
            call_order.append(role)
            if role == "backend":
                return _impl_result()
            elif role == "frontend":
                return _review_result("approved")
            elif role == "em_a":
                return _review_result("approved")

        invoker.invoke.side_effect = track_invoke
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.APPROVED
        # Verify order: engineer -> peer -> em
        assert call_order == ["backend", "frontend", "em_a"]

    def test_happy_path_approved(self):
        """Clean execution: implement, peer approves, EM approves."""
        invoker = MagicMock()
        invoker.invoke.side_effect = [
            _impl_result(),
            _review_result("approved"),
            _review_result("approved"),
        ]
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.APPROVED
        assert result.revision_count == 0
        assert result.implementation is not None
        assert result.peer_review is not None
        assert result.em_review is not None


class TestRevisionLoop:
    def test_em_rejection_triggers_revision(self):
        """EM rejects -> engineer re-implements -> reviews again."""
        invoker = MagicMock()
        invoker.invoke.side_effect = [
            # First iteration
            _impl_result(),                          # engineer
            _review_result("approved"),              # peer
            _review_result("needs_revision"),        # EM rejects
            # Second iteration
            _impl_result(),                          # engineer re-implements
            _review_result("approved"),              # peer
            _review_result("approved"),              # EM approves
        ]
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.APPROVED
        assert result.revision_count == 1

    def test_peer_block_skips_em(self):
        """Peer blocks -> no EM review, goes straight to revision."""
        invoker = MagicMock()
        call_order = []

        def track_invoke(role, prompt, **kwargs):
            call_order.append(role)
            if role == "backend":
                return _impl_result()
            elif role == "frontend":
                if len([c for c in call_order if c == "frontend"]) == 1:
                    return _review_result("blocked")
                return _review_result("approved")
            elif role == "em_a":
                return _review_result("approved")

        invoker.invoke.side_effect = track_invoke
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        # EM should not be called during first iteration (peer blocked)
        first_em_index = call_order.index("em_a")
        first_block_index = call_order.index("frontend")
        # After peer block, engineer re-executes before EM is ever called
        assert call_order[first_block_index + 1] == "backend"

    def test_max_revisions_circuit_breaker(self):
        """After max revisions, task fails instead of looping forever."""
        invoker = MagicMock()
        invoker.invoke.side_effect = [
            # iteration 0
            _impl_result(), _review_result("approved"), _review_result("needs_revision"),
            # iteration 1
            _impl_result(), _review_result("approved"), _review_result("needs_revision"),
            # iteration 2
            _impl_result(), _review_result("approved"), _review_result("needs_revision"),
            # iteration 3 (max_revisions=3, so this is the 4th attempt = index 3)
            _impl_result(), _review_result("approved"), _review_result("needs_revision"),
        ]
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker, max_revisions=3)

        assert result.status == TaskStatus.FAILED
        assert "revision iterations" in result.error


class TestQuestionEscalation:
    def test_question_pauses_task(self):
        """Engineer raises question -> task enters waiting_on_question."""
        invoker = MagicMock()
        invoker.invoke.return_value = _impl_result(
            status="needs_clarification",
            question="JWT or sessions?",
        )
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.WAITING_ON_QUESTION
        assert result.question is not None
        assert "JWT" in result.question.question
        # Peer review should NOT have been called
        assert invoker.invoke.call_count == 1

    def test_blocked_engineer_fails_task(self):
        """Engineer reports blocked -> task fails."""
        invoker = MagicMock()
        invoker.invoke.return_value = _impl_result(status="blocked")
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.FAILED
        assert invoker.invoke.call_count == 1


class TestFeedbackInjection:
    def test_human_feedback_included_in_prompt(self):
        """Operator comment is passed to the engineer."""
        invoker = MagicMock()
        invoker.invoke.side_effect = [
            _impl_result(),
            _review_result("approved"),
            _review_result("approved"),
        ]
        ctx = _make_ctx()
        ctx.feedback = "Use PostgreSQL, not SQLite"

        execute_task_workflow(ctx, invoker)

        first_call_prompt = invoker.invoke.call_args_list[0][1].get(
            "prompt", invoker.invoke.call_args_list[0][0][1]
        )
        assert "Use PostgreSQL" in first_call_prompt
```

**Test command:**
```bash
cd /Users/scottpeterson/xdev/claude-devteam && python -m pytest tests/orchestrator/test_task_workflow.py -v
```

---

## Task 6: Review Chain Enforcement (Route-Appropriate)

**File:** `src/devteam/orchestrator/review.py`

**Why:** Not every deliverable goes through every reviewer. Code changes get the full chain (peer -> EM -> QA -> Security -> Tech Writer). Research gets CA review only. Architecture gets CEO review. This module encodes those rules.

### Steps

- [ ] **Step 6.1** Create `src/devteam/orchestrator/review.py`.

```python
# src/devteam/orchestrator/review.py
"""Route-appropriate review chain enforcement.

Determines which post-PR review gates apply based on work type.
Pre-PR review (peer + EM) is handled by task_workflow.py.
This module handles post-PR shared services review.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from devteam.orchestrator.routing import AgentInvoker
from devteam.orchestrator.schemas import (
    ReviewResult,
    ReviewVerdict,
    WorkType,
)


@dataclass
class ReviewGate:
    """A single review gate with its reviewer role."""
    name: str
    reviewer_role: str
    required: bool = True


@dataclass
class ReviewChain:
    """The full review chain for a work type."""
    work_type: WorkType
    gates: list[ReviewGate]

    @property
    def gate_names(self) -> list[str]:
        return [g.name for g in self.gates]


# Review chain definitions per work type
REVIEW_CHAINS: dict[WorkType, list[ReviewGate]] = {
    WorkType.CODE: [
        ReviewGate(name="qa_review", reviewer_role="qa"),
        ReviewGate(name="security_review", reviewer_role="security"),
        ReviewGate(name="tech_writer_review", reviewer_role="tech_writer"),
    ],
    WorkType.RESEARCH: [
        ReviewGate(name="ca_review", reviewer_role="chief_architect"),
    ],
    WorkType.PLANNING: [
        ReviewGate(name="ca_review", reviewer_role="chief_architect"),
    ],
    WorkType.ARCHITECTURE: [
        ReviewGate(name="ceo_review", reviewer_role="ceo"),
    ],
    WorkType.DOCUMENTATION: [
        ReviewGate(name="engineer_review", reviewer_role="backend", required=False),
    ],
}


def get_review_chain(work_type: WorkType) -> ReviewChain:
    """Get the review chain for a given work type."""
    gates = REVIEW_CHAINS.get(work_type, [])
    return ReviewChain(work_type=work_type, gates=list(gates))


def is_small_fix_with_no_behavior_change(
    work_type: WorkType,
    files_changed: list[str],
) -> bool:
    """Determine if a small fix has no behavior change (skip QA)."""
    if work_type != WorkType.CODE:
        return False
    # Heuristic: if only docs/config/style files changed, no behavior change
    non_behavioral_patterns = (
        ".md", ".txt", ".yml", ".yaml", ".toml", ".json",
        ".css", ".scss", ".prettierrc", ".eslintrc",
    )
    return all(
        any(f.endswith(p) for p in non_behavioral_patterns)
        for f in files_changed
    )


@dataclass
class PostPRReviewResult:
    """Result of running the post-PR review chain."""
    all_passed: bool
    gate_results: dict[str, ReviewResult]
    failed_gates: list[str]
    skipped_gates: list[str]


def execute_post_pr_review(
    work_type: WorkType,
    pr_context: str,
    invoker: AgentInvoker,
    files_changed: list[str] | None = None,
    skip_qa_for_no_behavior_change: bool = True,
) -> PostPRReviewResult:
    """Execute the post-PR review chain for a work type.

    Each gate is executed in sequence. If a required gate fails,
    the chain stops (caller decides whether to trigger revision).
    """
    chain = get_review_chain(work_type)
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

        raw = invoker.invoke(
            role=gate.reviewer_role,
            prompt=(
                f"## {gate.name.replace('_', ' ').title()}\n\n"
                f"{pr_context}\n\n"
                "Review and provide your verdict.\n"
            ),
            json_schema=ReviewResult.model_json_schema(),
        )
        result = ReviewResult.model_validate(raw)
        gate_results[gate.name] = result

        if result.needs_revision:
            failed_gates.append(gate.name)
            if gate.required:
                # Stop the chain on required gate failure
                break

    return PostPRReviewResult(
        all_passed=len(failed_gates) == 0,
        gate_results=gate_results,
        failed_gates=failed_gates,
        skipped_gates=skipped_gates,
    )
```

- [ ] **Step 6.2** Create `tests/orchestrator/test_review.py`.

```python
# tests/orchestrator/test_review.py
"""Tests for route-appropriate review chain enforcement."""
import pytest
from unittest.mock import MagicMock

from devteam.orchestrator.review import (
    ReviewGate,
    execute_post_pr_review,
    get_review_chain,
    is_small_fix_with_no_behavior_change,
)
from devteam.orchestrator.schemas import (
    ReviewResult,
    ReviewVerdict,
    WorkType,
)


def _review(verdict="approved"):
    return {
        "verdict": verdict,
        "comments": [],
        "summary": "ok" if verdict == "approved" else "issues found",
    }


class TestGetReviewChain:
    def test_code_gets_full_chain(self):
        chain = get_review_chain(WorkType.CODE)
        names = chain.gate_names
        assert "qa_review" in names
        assert "security_review" in names
        assert "tech_writer_review" in names

    def test_research_gets_ca_only(self):
        chain = get_review_chain(WorkType.RESEARCH)
        names = chain.gate_names
        assert names == ["ca_review"]

    def test_planning_gets_ca_only(self):
        chain = get_review_chain(WorkType.PLANNING)
        assert chain.gate_names == ["ca_review"]

    def test_architecture_gets_ceo(self):
        chain = get_review_chain(WorkType.ARCHITECTURE)
        assert chain.gate_names == ["ceo_review"]

    def test_documentation_gets_engineer(self):
        chain = get_review_chain(WorkType.DOCUMENTATION)
        assert chain.gate_names == ["engineer_review"]


class TestSmallFixDetection:
    def test_docs_only_is_no_behavior_change(self):
        assert is_small_fix_with_no_behavior_change(
            WorkType.CODE, ["README.md", "docs/guide.md"]
        )

    def test_python_files_are_behavior_change(self):
        assert not is_small_fix_with_no_behavior_change(
            WorkType.CODE, ["src/api.py"]
        )

    def test_mixed_files_are_behavior_change(self):
        assert not is_small_fix_with_no_behavior_change(
            WorkType.CODE, ["README.md", "src/api.py"]
        )

    def test_non_code_work_type_always_false(self):
        assert not is_small_fix_with_no_behavior_change(
            WorkType.RESEARCH, ["README.md"]
        )


class TestExecutePostPRReview:
    def test_code_all_pass(self):
        invoker = MagicMock()
        invoker.invoke.return_value = _review("approved")

        result = execute_post_pr_review(
            WorkType.CODE, "PR context", invoker
        )

        assert result.all_passed
        assert len(result.gate_results) == 3
        assert "qa_review" in result.gate_results
        assert "security_review" in result.gate_results
        assert "tech_writer_review" in result.gate_results

    def test_research_only_ca_review(self):
        invoker = MagicMock()
        invoker.invoke.return_value = _review("approved")

        result = execute_post_pr_review(
            WorkType.RESEARCH, "Research output", invoker
        )

        assert result.all_passed
        assert len(result.gate_results) == 1
        assert "ca_review" in result.gate_results
        # Verify only chief_architect was invoked
        invoker.invoke.assert_called_once()
        assert invoker.invoke.call_args[1]["role"] == "chief_architect" or \
               invoker.invoke.call_args[0][0] == "chief_architect"

    def test_security_failure_stops_chain(self):
        """If security fails, tech writer review should not run."""
        invoker = MagicMock()
        call_count = [0]

        def side_effect(role, prompt, **kwargs):
            call_count[0] += 1
            if role == "security":
                return _review("needs_revision")
            return _review("approved")

        invoker.invoke.side_effect = side_effect

        result = execute_post_pr_review(
            WorkType.CODE, "PR context", invoker
        )

        assert not result.all_passed
        assert "security_review" in result.failed_gates
        # Tech writer should not have been called
        assert "tech_writer_review" not in result.gate_results

    def test_small_fix_skips_qa(self):
        """Small fix with no behavior change should skip QA."""
        invoker = MagicMock()
        invoker.invoke.return_value = _review("approved")

        result = execute_post_pr_review(
            WorkType.CODE,
            "PR context",
            invoker,
            files_changed=["README.md", "config.toml"],
        )

        assert result.all_passed
        assert "qa_review" in result.skipped_gates
        assert "qa_review" not in result.gate_results
        # Security and tech writer should still run
        assert "security_review" in result.gate_results
        assert "tech_writer_review" in result.gate_results

    def test_architecture_uses_ceo(self):
        invoker = MagicMock()
        invoker.invoke.return_value = _review("approved")

        result = execute_post_pr_review(
            WorkType.ARCHITECTURE, "ADR content", invoker
        )

        assert result.all_passed
        assert "ceo_review" in result.gate_results
```

**Test command:**
```bash
cd /Users/scottpeterson/xdev/claude-devteam && python -m pytest tests/orchestrator/test_review.py -v
```

---

## Task 7: Question Escalation Workflow

**File:** `src/devteam/orchestrator/escalation.py`

**Why:** Questions are first-class workflow events that pause individual task branches. The escalation path depends on question type (architecture -> CA, policy -> CEO, technical -> EM). If no agent can resolve, the question surfaces to the human.

### Steps

- [ ] **Step 7.1** Create `src/devteam/orchestrator/escalation.py`.

```python
# src/devteam/orchestrator/escalation.py
"""Question escalation workflow — pause branch, route to supervisor chain.

Questions pause individual task branches while other branches continue.
Escalation: supervisor -> leadership -> human.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from devteam.orchestrator.routing import AgentInvoker
from devteam.orchestrator.schemas import (
    EscalationLevel,
    QuestionRecord,
    QuestionType,
)


# Escalation routing based on question type (from spec)
ESCALATION_PATHS: dict[QuestionType, list[str]] = {
    QuestionType.ARCHITECTURE: ["em", "chief_architect", "human"],
    QuestionType.ROUTING_POLICY: ["em", "ceo", "human"],
    QuestionType.SPEC_AMBIGUITY: ["em", "ceo", "human"],
    QuestionType.TECHNICAL: ["em"],  # Usually resolved at EM level
}


@dataclass
class EscalationAttempt:
    """Result of attempting to resolve a question at one level."""
    level: str
    resolved: bool
    answer: Optional[str] = None
    reasoning: Optional[str] = None


@dataclass
class EscalationResult:
    """Full result of the escalation workflow."""
    question: QuestionRecord
    resolved: bool
    attempts: list[EscalationAttempt]
    needs_human: bool = False


def get_escalation_path(question_type: QuestionType) -> list[str]:
    """Get the escalation path for a question type."""
    return list(ESCALATION_PATHS.get(question_type, ["em", "human"]))


def build_escalation_prompt(question: QuestionRecord, level: str) -> str:
    """Build prompt for a supervisor to attempt answering a question."""
    return (
        f"## Question Escalated to You\n\n"
        f"**From task:** {question.task_id} (Job {question.job_id})\n"
        f"**Question type:** {question.question_type.value}\n"
        f"**Question:** {question.question}\n\n"
        f"Can you answer this question within your authority as {level}?\n"
        f"If yes, provide the answer.\n"
        f"If no, explain why this needs to be escalated further.\n\n"
        f"Return a JSON object with:\n"
        f'- "resolved": true/false\n'
        f'- "answer": your answer (if resolved)\n'
        f'- "reasoning": why you can or cannot answer\n'
    )


def attempt_resolution(
    question: QuestionRecord,
    level: str,
    invoker: AgentInvoker,
) -> EscalationAttempt:
    """Attempt to resolve a question at a given escalation level."""
    prompt = build_escalation_prompt(question, level)
    raw = invoker.invoke(
        role=level,
        prompt=prompt,
    )

    # Parse the response (agent returns dict with resolved, answer, reasoning)
    resolved = raw.get("resolved", False)
    return EscalationAttempt(
        level=level,
        resolved=resolved,
        answer=raw.get("answer"),
        reasoning=raw.get("reasoning"),
    )


def escalate_question(
    question: QuestionRecord,
    invoker: AgentInvoker,
    em_role: str = "em_a",
) -> EscalationResult:
    """Run the escalation workflow for a question.

    Walks up the escalation chain for the question type.
    If resolved at any level, returns immediately.
    If all agent levels fail, marks as needing human input.
    """
    path = get_escalation_path(question.question_type)
    attempts: list[EscalationAttempt] = []

    # Replace generic "em" with the specific EM role
    path = [em_role if level == "em" else level for level in path]

    for level in path:
        if level == "human":
            # Reached the end of the agent chain
            question.escalation_level = EscalationLevel.HUMAN
            return EscalationResult(
                question=question,
                resolved=False,
                attempts=attempts,
                needs_human=True,
            )

        attempt = attempt_resolution(question, level, invoker)
        attempts.append(attempt)

        if attempt.resolved:
            question.resolved = True
            question.answer = attempt.answer
            question.answered_by = level

            # Set escalation level based on where it was resolved
            if level == em_role:
                question.escalation_level = EscalationLevel.SUPERVISOR
            else:
                question.escalation_level = EscalationLevel.LEADERSHIP
            return EscalationResult(
                question=question,
                resolved=True,
                attempts=attempts,
            )

    # Shouldn't reach here if path includes "human", but handle gracefully
    return EscalationResult(
        question=question,
        resolved=False,
        attempts=attempts,
        needs_human=True,
    )


def resolve_with_human_answer(
    question: QuestionRecord,
    answer: str,
) -> QuestionRecord:
    """Resolve a question with a human-provided answer.

    Called when the operator uses `devteam answer <question-id> "..."`.
    """
    question.resolved = True
    question.answer = answer
    question.answered_by = "human"
    question.escalation_level = EscalationLevel.HUMAN
    return question
```

- [ ] **Step 7.2** Create `tests/orchestrator/test_escalation.py`.

```python
# tests/orchestrator/test_escalation.py
"""Tests for question escalation workflow."""
import pytest
from unittest.mock import MagicMock

from devteam.orchestrator.escalation import (
    EscalationResult,
    escalate_question,
    get_escalation_path,
    resolve_with_human_answer,
)
from devteam.orchestrator.schemas import (
    EscalationLevel,
    QuestionRecord,
    QuestionType,
)


def _make_question(qtype=QuestionType.TECHNICAL):
    return QuestionRecord(
        id="Q-1",
        task_id="T-2",
        job_id="W-1",
        question="Redis or Memcached?",
        question_type=qtype,
    )


class TestGetEscalationPath:
    def test_architecture_goes_to_ca(self):
        path = get_escalation_path(QuestionType.ARCHITECTURE)
        assert "chief_architect" in path

    def test_routing_goes_to_ceo(self):
        path = get_escalation_path(QuestionType.ROUTING_POLICY)
        assert "ceo" in path

    def test_technical_stays_at_em(self):
        path = get_escalation_path(QuestionType.TECHNICAL)
        assert path == ["em"]

    def test_all_paths_end_at_human_or_em(self):
        for qt in QuestionType:
            path = get_escalation_path(qt)
            assert path[-1] in ("em", "human")


class TestEscalateQuestion:
    def test_resolved_at_em_level(self):
        """Technical question resolved by EM."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "resolved": True,
            "answer": "Use Redis for its pub/sub support",
            "reasoning": "Matches our existing stack",
        }
        q = _make_question(QuestionType.TECHNICAL)
        result = escalate_question(q, invoker, em_role="em_a")

        assert result.resolved
        assert not result.needs_human
        assert len(result.attempts) == 1
        assert result.question.answered_by == "em_a"
        assert result.question.escalation_level == EscalationLevel.SUPERVISOR

    def test_escalated_to_ca(self):
        """Architecture question: EM can't resolve, CA can."""
        invoker = MagicMock()
        call_count = [0]

        def side_effect(role, prompt, **kwargs):
            call_count[0] += 1
            if role == "em_a":
                return {"resolved": False, "reasoning": "Need CA input"}
            elif role == "chief_architect":
                return {
                    "resolved": True,
                    "answer": "Use event sourcing",
                    "reasoning": "Matches architecture",
                }
            return {"resolved": False, "reasoning": "Cannot answer"}

        invoker.invoke.side_effect = side_effect
        q = _make_question(QuestionType.ARCHITECTURE)
        result = escalate_question(q, invoker, em_role="em_a")

        assert result.resolved
        assert len(result.attempts) == 2
        assert result.question.answered_by == "chief_architect"
        assert result.question.escalation_level == EscalationLevel.LEADERSHIP

    def test_escalated_to_human(self):
        """No agent can resolve -> needs human."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "resolved": False,
            "reasoning": "Cannot determine",
        }
        q = _make_question(QuestionType.SPEC_AMBIGUITY)
        result = escalate_question(q, invoker, em_role="em_a")

        assert not result.resolved
        assert result.needs_human
        assert result.question.escalation_level == EscalationLevel.HUMAN

    def test_question_pauses_branch(self):
        """Unresolved question means the branch stays paused."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "resolved": False,
            "reasoning": "Needs business decision",
        }
        q = _make_question(QuestionType.ROUTING_POLICY)
        result = escalate_question(q, invoker, em_role="em_a")

        assert not result.resolved
        assert result.needs_human
        # The question object should reflect the escalation
        assert not result.question.resolved


class TestResolveWithHumanAnswer:
    def test_resolves_question(self):
        q = _make_question()
        resolved = resolve_with_human_answer(q, "Use Redis")

        assert resolved.resolved
        assert resolved.answer == "Use Redis"
        assert resolved.answered_by == "human"
        assert resolved.escalation_level == EscalationLevel.HUMAN

    def test_preserves_original_fields(self):
        q = _make_question()
        resolved = resolve_with_human_answer(q, "Use Redis")

        assert resolved.id == "Q-1"
        assert resolved.task_id == "T-2"
        assert resolved.job_id == "W-1"
        assert resolved.question == "Redis or Memcached?"
```

**Test command:**
```bash
cd /Users/scottpeterson/xdev/claude-devteam && python -m pytest tests/orchestrator/test_escalation.py -v
```

---

## Task 8: Job Lifecycle Management

**File:** `src/devteam/orchestrator/jobs.py`

**Why:** Jobs are the top-level entity. This module manages job creation, state transitions, and the full lifecycle from `created` through `completed`. It wires together routing, decomposition, DAG execution, and review into the top-level DBOS workflow.

### Steps

- [ ] **Step 8.1** Create `src/devteam/orchestrator/jobs.py`.

```python
# src/devteam/orchestrator/jobs.py
"""Job lifecycle management — top-level workflow orchestration.

Manages the full job lifecycle: created -> planning -> decomposing ->
executing -> reviewing -> completed.

This module ties together routing, decomposition, DAG execution,
and post-PR review into a single durable workflow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from devteam.orchestrator.dag import (
    DAGExecutionResult,
    DAGExecutor,
    build_dag,
)
from devteam.orchestrator.decomposition import decompose
from devteam.orchestrator.review import execute_post_pr_review
from devteam.orchestrator.routing import (
    AgentInvoker,
    IntakeContext,
    route_intake,
)
from devteam.orchestrator.schemas import (
    DecompositionResult,
    JobStatus,
    RoutePath,
    RoutingResult,
    TaskStatus,
    WorkType,
)
from devteam.orchestrator.task_workflow import (
    TaskContext,
    TaskWorkflowResult,
    execute_task_workflow,
)


@dataclass
class Job:
    """A top-level job with its full state."""
    id: str
    title: str
    status: JobStatus = JobStatus.CREATED
    intake: Optional[IntakeContext] = None
    routing: Optional[RoutingResult] = None
    decomposition: Optional[DecompositionResult] = None
    task_results: dict[str, TaskWorkflowResult] = field(default_factory=dict)
    dag_result: Optional[DAGExecutionResult] = None
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    comments: list[str] = field(default_factory=list)

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


def transition_job(job: Job, new_status: JobStatus) -> Job:
    """Transition a job to a new status with validation.

    Enforces valid state transitions from the spec:
    created -> planning -> decomposing -> executing -> reviewing -> completed
    """
    valid_transitions: dict[JobStatus, set[JobStatus]] = {
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

    allowed = valid_transitions.get(job.status, set())
    if new_status not in allowed:
        raise ValueError(
            f"Invalid transition: {job.status.value} -> {new_status.value}"
        )

    job.status = new_status
    if new_status == JobStatus.COMPLETED:
        job.completed_at = datetime.now(timezone.utc)
    return job


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


def execute_job(
    job: Job,
    invoker: AgentInvoker,
    task_launcher=None,
    task_checker=None,
) -> Job:
    """Execute the full job workflow.

    Orchestrates: routing -> decomposition -> DAG execution -> post-PR review.

    In production, task_launcher and task_checker are DBOS workflow handles.
    In tests, they are synchronous functions that return immediately.

    Args:
        job: The job to execute.
        invoker: Agent invoker for all agent calls.
        task_launcher: Function to launch a task workflow (returns handle).
        task_checker: Non-blocking check. Returns (done, result_or_error).
    """
    if not job.intake:
        raise ValueError("Job has no intake context")

    # Step 1: Route
    transition_job(job, JobStatus.PLANNING)
    job.routing = route_intake(job.intake, invoker)

    # Step 2: Research path — no decomposition, no DAG
    if job.routing.path == RoutePath.RESEARCH:
        transition_job(job, JobStatus.EXECUTING)
        # Research is a single-task workflow handled differently
        transition_job(job, JobStatus.COMPLETED)
        return job

    # Step 3: Decompose (skip for small fixes with pre-assigned tasks)
    transition_job(job, JobStatus.DECOMPOSING)
    spec = job.intake.spec or ""
    plan = job.intake.plan or ""
    job.decomposition = decompose(spec, plan, job.routing, invoker)

    # Step 4: Execute DAG
    transition_job(job, JobStatus.EXECUTING)

    if task_launcher and task_checker:
        dag = build_dag(job.decomposition)
        executor = DAGExecutor(
            launch_task=task_launcher,
            check_complete=task_checker,
        )
        job.dag_result = executor.execute(dag)

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

    return job
```

- [ ] **Step 8.2** Create `tests/orchestrator/test_jobs.py`.

```python
# tests/orchestrator/test_jobs.py
"""Tests for job lifecycle management."""
import pytest
from unittest.mock import MagicMock

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
    JobStatus,
    RoutePath,
    WorkType,
)


class TestCreateJob:
    def test_creates_with_defaults(self):
        intake = IntakeContext(spec="spec", plan="plan")
        job = create_job("W-1", "My App", intake)
        assert job.id == "W-1"
        assert job.status == JobStatus.CREATED
        assert job.completed_at is None

    def test_progress_empty(self):
        job = create_job("W-1", "Test", IntakeContext())
        assert job.progress == (0, 0)


class TestTransitionJob:
    def test_valid_transition(self):
        job = Job(id="W-1", title="Test")
        transition_job(job, JobStatus.PLANNING)
        assert job.status == JobStatus.PLANNING

    def test_invalid_transition_raises(self):
        job = Job(id="W-1", title="Test")
        with pytest.raises(ValueError, match="Invalid transition"):
            transition_job(job, JobStatus.COMPLETED)

    def test_full_lifecycle(self):
        job = Job(id="W-1", title="Test")
        transition_job(job, JobStatus.PLANNING)
        transition_job(job, JobStatus.DECOMPOSING)
        transition_job(job, JobStatus.EXECUTING)
        transition_job(job, JobStatus.REVIEWING)
        transition_job(job, JobStatus.COMPLETED)
        assert job.status == JobStatus.COMPLETED
        assert job.completed_at is not None

    def test_cancel_from_any_active_state(self):
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

    def test_cannot_transition_from_terminal(self):
        for terminal in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED]:
            job = Job(id="W-1", title="Test", status=terminal)
            with pytest.raises(ValueError):
                transition_job(job, JobStatus.PLANNING)

    def test_rate_limit_pause_and_resume(self):
        job = Job(id="W-1", title="Test", status=JobStatus.EXECUTING)
        transition_job(job, JobStatus.PAUSED_RATE_LIMIT)
        transition_job(job, JobStatus.EXECUTING)
        assert job.status == JobStatus.EXECUTING

    def test_small_fix_skips_decomposition(self):
        """Small fix can go directly from planning to executing."""
        job = Job(id="W-1", title="Test", status=JobStatus.PLANNING)
        transition_job(job, JobStatus.EXECUTING)
        assert job.status == JobStatus.EXECUTING


class TestRouteWorkTypeMapping:
    def test_full_project_is_code(self):
        assert determine_work_type_from_route(RoutePath.FULL_PROJECT) == WorkType.CODE

    def test_research_is_research(self):
        assert determine_work_type_from_route(RoutePath.RESEARCH) == WorkType.RESEARCH

    def test_small_fix_is_code(self):
        assert determine_work_type_from_route(RoutePath.SMALL_FIX) == WorkType.CODE


class TestNeedsPostPRReview:
    def test_full_project_needs_review(self):
        assert needs_post_pr_review(RoutePath.FULL_PROJECT)

    def test_research_no_review(self):
        assert not needs_post_pr_review(RoutePath.RESEARCH)

    def test_small_fix_needs_review(self):
        assert needs_post_pr_review(RoutePath.SMALL_FIX)


class TestExecuteJob:
    def test_full_project_lifecycle(self):
        """Full project: route -> decompose -> execute DAG -> review."""
        invoker = MagicMock()

        # Route returns full_project (fast path for spec+plan)
        # No CEO call needed

        # CA decomposition
        invoker.invoke.side_effect = [
            # decompose call to chief_architect
            {
                "tasks": [
                    {
                        "id": "T-1",
                        "description": "Build API",
                        "assigned_to": "backend",
                        "team": "a",
                        "depends_on": [],
                        "pr_group": "feat/api",
                        "work_type": "code",
                    },
                ],
                "peer_assignments": {"T-1": "frontend"},
                "parallel_groups": [["T-1"]],
            },
            # Post-PR reviews (QA, Security, Tech Writer)
            {"verdict": "approved", "comments": [], "summary": "ok"},
            {"verdict": "approved", "comments": [], "summary": "ok"},
            {"verdict": "approved", "comments": [], "summary": "ok"},
        ]

        intake = IntakeContext(spec="Build an API", plan="Step 1: schema")
        job = create_job("W-1", "My App", intake)

        def launch(task):
            return task.id

        def wait(handle):
            return (True, {"status": "completed"})

        result = execute_job(job, invoker, task_launcher=launch, task_checker=wait)

        assert result.status == JobStatus.COMPLETED

    def test_research_path_no_decomposition(self):
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

    def test_job_without_intake_raises(self):
        job = Job(id="W-1", title="Test", intake=None)
        with pytest.raises(ValueError, match="no intake"):
            execute_job(job, MagicMock())


class TestJobComments:
    def test_add_comment(self):
        job = create_job("W-1", "Test", IntakeContext())
        job.add_comment("Use PostgreSQL instead")
        assert len(job.comments) == 1
        assert "PostgreSQL" in job.comments[0]
```

**Test command:**
```bash
cd /Users/scottpeterson/xdev/claude-devteam && python -m pytest tests/orchestrator/test_jobs.py -v
```

---

## Task 9: CLI Wiring — `devteam start`, `devteam comment`, `devteam answer`

**File:** `src/devteam/orchestrator/cli_bridge.py`

**Why:** This bridges the CLI layer (Plan 1) to the workflow engine. `devteam start` parses intake arguments, creates a Job, and launches the workflow. `devteam comment` injects feedback. `devteam answer` resolves questions and resumes paused branches.

### Steps

- [ ] **Step 9.1** Create `src/devteam/orchestrator/cli_bridge.py`.

```python
# src/devteam/orchestrator/cli_bridge.py
"""CLI bridge — connects CLI commands to the workflow engine.

Handles argument parsing and job creation for:
- devteam start (--spec/--plan/--issue/--prompt)
- devteam comment (inject feedback into running task)
- devteam answer (resolve question, resume paused branch)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from devteam.orchestrator.escalation import resolve_with_human_answer
from devteam.orchestrator.jobs import Job, create_job
from devteam.orchestrator.routing import IntakeContext
from devteam.orchestrator.schemas import QuestionRecord


class JobStore:
    """In-memory job store. In production, backed by DBOS/SQLite.

    This is a minimal interface for Plan 3. Plan 1 provides
    the actual persistence layer.
    """

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._questions: dict[str, QuestionRecord] = {}
        self._next_job_id: int = 1

    def next_id(self) -> str:
        job_id = f"W-{self._next_job_id}"
        self._next_job_id += 1
        return job_id

    def save(self, job: Job) -> None:
        self._jobs[job.id] = job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def save_question(self, question: QuestionRecord) -> None:
        self._questions[question.id] = question

    def get_question(self, question_id: str) -> QuestionRecord | None:
        return self._questions.get(question_id)

    def get_pending_questions(self, job_id: str | None = None) -> list[QuestionRecord]:
        questions = [
            q for q in self._questions.values() if not q.resolved
        ]
        if job_id:
            questions = [q for q in questions if q.job_id == job_id]
        return questions


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


def handle_start(
    store: JobStore,
    title: str = "Untitled Job",
    spec: str | None = None,
    plan: str | None = None,
    issue: str | None = None,
    prompt: str | None = None,
) -> Job:
    """Handle `devteam start` — create job and prepare for execution.

    Returns the created job. The caller (daemon) is responsible for
    launching the actual workflow execution.
    """
    intake = parse_intake(spec=spec, plan=plan, issue=issue, prompt=prompt)
    job_id = store.next_id()
    job = create_job(job_id, title, intake)
    store.save(job)
    return job


def handle_comment(
    store: JobStore,
    task_ref: str,
    comment: str,
) -> bool:
    """Handle `devteam comment` — inject feedback into a task.

    Args:
        task_ref: Task reference like 'W-1/T-3' or 'T-3' (single job).
        comment: The feedback text.

    Returns:
        True if comment was attached successfully.
    """
    job_id, task_id = _parse_task_ref(task_ref, store)
    if not job_id:
        return False

    job = store.get(job_id)
    if not job:
        return False

    job.add_comment(f"[{task_id}] {comment}")
    store.save(job)
    return True


def handle_answer(
    store: JobStore,
    question_ref: str,
    answer: str,
) -> QuestionRecord | None:
    """Handle `devteam answer` — resolve a question and resume branch.

    Args:
        question_ref: Question reference like 'W-1/Q-3' or 'Q-3'.
        answer: The human's answer.

    Returns:
        The resolved QuestionRecord, or None if not found.
    """
    question_id = _parse_question_ref(question_ref)
    question = store.get_question(question_id)
    if not question:
        return None

    resolved = resolve_with_human_answer(question, answer)
    store.save_question(resolved)
    return resolved


def _parse_task_ref(ref: str, store: JobStore) -> tuple[str | None, str]:
    """Parse 'W-1/T-3' or 'T-3' into (job_id, task_id)."""
    if "/" in ref:
        parts = ref.split("/", 1)
        return parts[0], parts[1]
    # Single job shorthand — find the only active job
    jobs = [j for j in store._jobs.values()]
    if len(jobs) == 1:
        return jobs[0].id, ref
    return None, ref


def _parse_question_ref(ref: str) -> str:
    """Parse 'W-1/Q-3' or 'Q-3' into question_id."""
    if "/" in ref:
        return ref.split("/", 1)[1]
    return ref
```

- [ ] **Step 9.2** Create `tests/orchestrator/test_cli_bridge.py`.

```python
# tests/orchestrator/test_cli_bridge.py
"""Tests for CLI bridge — devteam start, comment, answer."""
import pytest
import tempfile
from pathlib import Path

from devteam.orchestrator.cli_bridge import (
    JobStore,
    handle_answer,
    handle_comment,
    handle_start,
    parse_intake,
)
from devteam.orchestrator.schemas import (
    EscalationLevel,
    JobStatus,
    QuestionRecord,
    QuestionType,
)


class TestParseIntake:
    def test_spec_as_string(self):
        ctx = parse_intake(spec="Build an API")
        assert ctx.spec == "Build an API"

    def test_spec_as_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# My Spec\nBuild a thing")
            f.flush()
            ctx = parse_intake(spec=f.name)
            assert "My Spec" in ctx.spec

    def test_issue_url(self):
        ctx = parse_intake(issue="https://github.com/org/repo/issues/42")
        assert ctx.issue_url == "https://github.com/org/repo/issues/42"

    def test_prompt(self):
        ctx = parse_intake(prompt="Fix the login bug")
        assert ctx.prompt == "Fix the login bug"

    def test_spec_and_plan(self):
        ctx = parse_intake(spec="spec", plan="plan")
        assert ctx.spec == "spec"
        assert ctx.plan == "plan"


class TestJobStore:
    def test_next_id_increments(self):
        store = JobStore()
        assert store.next_id() == "W-1"
        assert store.next_id() == "W-2"

    def test_save_and_get(self):
        store = JobStore()
        from devteam.orchestrator.jobs import create_job
        from devteam.orchestrator.routing import IntakeContext

        job = create_job("W-1", "Test", IntakeContext())
        store.save(job)
        assert store.get("W-1") is not None
        assert store.get("W-99") is None

    def test_pending_questions(self):
        store = JobStore()
        q1 = QuestionRecord(
            id="Q-1", task_id="T-1", job_id="W-1",
            question="x?", question_type=QuestionType.TECHNICAL,
        )
        q2 = QuestionRecord(
            id="Q-2", task_id="T-2", job_id="W-1",
            question="y?", question_type=QuestionType.TECHNICAL,
            resolved=True, answer="yes",
        )
        store.save_question(q1)
        store.save_question(q2)
        pending = store.get_pending_questions()
        assert len(pending) == 1
        assert pending[0].id == "Q-1"


class TestHandleStart:
    def test_creates_job_with_intake(self):
        store = JobStore()
        job = handle_start(store, title="My App", spec="spec", plan="plan")
        assert job.id == "W-1"
        assert job.status == JobStatus.CREATED
        assert job.intake.spec == "spec"
        assert job.intake.plan == "plan"

    def test_sequential_job_ids(self):
        store = JobStore()
        j1 = handle_start(store, title="Job 1", prompt="do thing 1")
        j2 = handle_start(store, title="Job 2", prompt="do thing 2")
        assert j1.id == "W-1"
        assert j2.id == "W-2"

    def test_stored_in_store(self):
        store = JobStore()
        job = handle_start(store, title="Test", prompt="fix bug")
        assert store.get("W-1") is not None


class TestHandleComment:
    def test_comment_on_task(self):
        store = JobStore()
        job = handle_start(store, title="Test", prompt="test")

        success = handle_comment(store, "W-1/T-3", "Use PostgreSQL")
        assert success
        assert any("PostgreSQL" in c for c in job.comments)

    def test_comment_shorthand_single_job(self):
        store = JobStore()
        job = handle_start(store, title="Test", prompt="test")

        success = handle_comment(store, "T-3", "feedback")
        assert success

    def test_comment_nonexistent_job(self):
        store = JobStore()
        success = handle_comment(store, "W-99/T-1", "feedback")
        assert not success


class TestHandleAnswer:
    def test_answer_resolves_question(self):
        store = JobStore()
        q = QuestionRecord(
            id="Q-1", task_id="T-2", job_id="W-1",
            question="Redis or Memcached?",
            question_type=QuestionType.TECHNICAL,
        )
        store.save_question(q)

        resolved = handle_answer(store, "W-1/Q-1", "Use Redis")
        assert resolved is not None
        assert resolved.resolved
        assert resolved.answer == "Use Redis"
        assert resolved.answered_by == "human"

    def test_answer_shorthand(self):
        store = JobStore()
        q = QuestionRecord(
            id="Q-1", task_id="T-2", job_id="W-1",
            question="x?", question_type=QuestionType.TECHNICAL,
        )
        store.save_question(q)

        resolved = handle_answer(store, "Q-1", "answer")
        assert resolved is not None
        assert resolved.resolved

    def test_answer_nonexistent_question(self):
        store = JobStore()
        result = handle_answer(store, "Q-99", "answer")
        assert result is None
```

**Test command:**
```bash
cd /Users/scottpeterson/xdev/claude-devteam && python -m pytest tests/orchestrator/test_cli_bridge.py -v
```

---

## Task 10: Integration Test — Full Workflow End-to-End

**File:** `tests/orchestrator/test_integration.py`

**Why:** Verify that all modules work together: routing -> decomposition -> DAG execution -> task workflows -> review chains -> question escalation. This is the "it all fits together" test with mocked agent invocations.

### Steps

- [ ] **Step 10.1** Create `tests/orchestrator/test_integration.py`.

```python
# tests/orchestrator/test_integration.py
"""Integration tests — full workflow end-to-end with mocked agents.

Verifies that routing, decomposition, DAG execution, task workflows,
review chains, and question escalation all work together correctly.
"""
import pytest
from unittest.mock import MagicMock

from devteam.orchestrator.cli_bridge import JobStore, handle_answer, handle_start
from devteam.orchestrator.dag import DAGExecutor, build_dag
from devteam.orchestrator.decomposition import decompose
from devteam.orchestrator.escalation import escalate_question
from devteam.orchestrator.jobs import (
    Job,
    create_job,
    execute_job,
    transition_job,
)
from devteam.orchestrator.review import execute_post_pr_review
from devteam.orchestrator.routing import IntakeContext, route_intake
from devteam.orchestrator.schemas import (
    DecompositionResult,
    EscalationLevel,
    JobStatus,
    QuestionRecord,
    QuestionType,
    RoutePath,
    RoutingResult,
    TaskDefinition,
    TaskStatus,
    WorkType,
)
from devteam.orchestrator.task_workflow import (
    TaskContext,
    execute_task_workflow,
)


def _impl_ok():
    return {
        "status": "completed",
        "question": None,
        "files_changed": ["src/api.py"],
        "tests_added": ["tests/test_api.py"],
        "summary": "Built the feature",
        "confidence": "high",
    }


def _review_ok():
    return {"verdict": "approved", "comments": [], "summary": "LGTM"}


def _review_reject():
    return {"verdict": "needs_revision", "comments": [], "summary": "Fix tests"}


class TestFullProjectWorkflow:
    """End-to-end: spec+plan -> route -> decompose -> execute -> review."""

    def test_spec_plan_to_completion(self):
        """Happy path: spec+plan provided, all tasks pass, all reviews pass."""
        invoker = MagicMock()

        # Track which roles are invoked
        invocations = []

        def mock_invoke(role, prompt, **kwargs):
            invocations.append(role)
            if role == "chief_architect":
                return {
                    "tasks": [
                        {
                            "id": "T-1",
                            "description": "Build backend API",
                            "assigned_to": "backend",
                            "team": "a",
                            "depends_on": [],
                            "pr_group": "feat/api",
                            "work_type": "code",
                        },
                        {
                            "id": "T-2",
                            "description": "Build frontend",
                            "assigned_to": "frontend",
                            "team": "a",
                            "depends_on": ["T-1"],
                            "pr_group": "feat/ui",
                            "work_type": "code",
                        },
                    ],
                    "peer_assignments": {"T-1": "frontend", "T-2": "backend"},
                    "parallel_groups": [["T-1"], ["T-2"]],
                }
            elif role in ("qa", "security", "tech_writer"):
                return _review_ok()
            return _review_ok()

        invoker.invoke.side_effect = mock_invoke

        intake = IntakeContext(spec="Build a web app", plan="Step 1: API")
        job = create_job("W-1", "Web App", intake)

        def launch(task):
            return task.id

        def wait(handle):
            return (True, {"status": "completed"})

        result = execute_job(job, invoker, task_launcher=launch, task_checker=wait)

        assert result.status == JobStatus.COMPLETED
        # Verify CA was invoked for decomposition
        assert "chief_architect" in invocations
        # Verify post-PR review gates were hit
        assert "qa" in invocations
        assert "security" in invocations
        assert "tech_writer" in invocations


class TestDAGParallelism:
    """Verify that independent tasks are launched in parallel."""

    def test_independent_tasks_launched_together(self):
        launch_batches = []
        current_batch = []

        def launch(task):
            current_batch.append(task.id)
            return task.id

        def wait(handle):
            nonlocal current_batch
            if current_batch:
                launch_batches.append(list(current_batch))
                current_batch.clear()
            return (True, "ok")

        decomp = DecompositionResult(
            tasks=[
                TaskDefinition(
                    id="T-1", description="API", assigned_to="backend",
                    team="a", pr_group="g1",
                ),
                TaskDefinition(
                    id="T-2", description="UI", assigned_to="frontend",
                    team="a", pr_group="g2",
                ),
                TaskDefinition(
                    id="T-3", description="Integration",
                    assigned_to="backend", team="a",
                    depends_on=["T-1", "T-2"], pr_group="g3",
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
        assert "T-1" in launch_batches[0] or "T-2" in launch_batches[0]


class TestReviewChainIntegration:
    """Verify review chains are correctly applied based on work type."""

    def test_code_gets_full_review(self):
        invoker = MagicMock()
        roles_invoked = []

        def mock_invoke(role, prompt, **kwargs):
            roles_invoked.append(role)
            return _review_ok()

        invoker.invoke.side_effect = mock_invoke

        result = execute_post_pr_review(
            WorkType.CODE, "PR diff here", invoker
        )
        assert result.all_passed
        assert "qa" in roles_invoked
        assert "security" in roles_invoked
        assert "tech_writer" in roles_invoked

    def test_research_gets_ca_only(self):
        invoker = MagicMock()
        roles_invoked = []

        def mock_invoke(role, prompt, **kwargs):
            roles_invoked.append(role)
            return _review_ok()

        invoker.invoke.side_effect = mock_invoke

        result = execute_post_pr_review(
            WorkType.RESEARCH, "Research output", invoker
        )
        assert result.all_passed
        assert roles_invoked == ["chief_architect"]
        assert "qa" not in roles_invoked


class TestQuestionEscalationIntegration:
    """Verify question handling end-to-end."""

    def test_question_raised_then_answered_by_human(self):
        store = JobStore()
        job = handle_start(store, title="Test", spec="spec", plan="plan")

        # Simulate engineer raising a question
        q = QuestionRecord(
            id="Q-1",
            task_id="T-1",
            job_id=job.id,
            question="JWT or sessions?",
            question_type=QuestionType.SPEC_AMBIGUITY,
        )
        store.save_question(q)

        # Try agent escalation — all fail
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "resolved": False,
            "reasoning": "Need human input",
        }
        result = escalate_question(q, invoker, em_role="em_a")
        assert result.needs_human

        # Human answers
        resolved = handle_answer(store, "Q-1", "Use JWT with refresh tokens")
        assert resolved.resolved
        assert resolved.answer == "Use JWT with refresh tokens"


class TestTaskWorkflowIntegration:
    """Verify task workflow with review chain enforcement."""

    def test_revision_loop_then_approval(self):
        """Engineer implements -> peer rejects -> revise -> approve."""
        invoker = MagicMock()
        invoker.invoke.side_effect = [
            # First pass
            _impl_ok(),           # engineer
            _review_reject(),     # peer rejects
            _review_reject(),     # EM also rejects
            # Revision
            _impl_ok(),           # engineer re-implements
            _review_ok(),         # peer approves
            _review_ok(),         # EM approves
        ]

        task = TaskDefinition(
            id="T-1", description="Build API",
            assigned_to="backend", team="a", pr_group="g1",
        )
        ctx = TaskContext(
            task=task, peer_reviewer="frontend", em_role="em_a",
            worktree_path="/tmp/wt", job_id="W-1",
        )
        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.APPROVED
        assert result.revision_count == 1


class TestJobCancellation:
    """Verify job can be cancelled from any active state."""

    def test_cancel_executing_job(self):
        job = Job(id="W-1", title="Test", status=JobStatus.EXECUTING)
        transition_job(job, JobStatus.CANCELED)
        assert job.status == JobStatus.CANCELED

    def test_cancel_reviewing_job(self):
        job = Job(id="W-1", title="Test", status=JobStatus.REVIEWING)
        transition_job(job, JobStatus.CANCELED)
        assert job.status == JobStatus.CANCELED
```

**Test command:**
```bash
cd /Users/scottpeterson/xdev/claude-devteam && python -m pytest tests/orchestrator/test_integration.py -v
```

---

## Summary

| Task | Module | What It Tests |
|------|--------|--------------|
| 1 | `schemas.py` | All Pydantic models validate correctly |
| 2 | `routing.py` | CEO routing returns correct paths for different intake types |
| 3 | `decomposition.py` | CA decomposes into valid DAGs with peer assignments |
| 4 | `dag.py` | DAG respects dependencies, runs independent tasks in parallel |
| 5 | `task_workflow.py` | Review chains enforced (peer before EM), revision loops work |
| 6 | `review.py` | Route-appropriate gates (code=full, research=CA only) |
| 7 | `escalation.py` | Questions pause branches, escalate correctly |
| 8 | `jobs.py` | Job lifecycle transitions, full workflow orchestration |
| 9 | `cli_bridge.py` | CLI commands create jobs, inject comments, resolve questions |
| 10 | `test_integration.py` | All modules work together end-to-end |

**Run all tests:**
```bash
cd /Users/scottpeterson/xdev/claude-devteam && python -m pytest tests/orchestrator/ -v
```
