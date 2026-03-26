"""Structured output contracts for agent invocations.

These Pydantic models define the JSON schemas that agents must conform to
when returning results. The orchestrator uses these to machine-parse agent
output without prose parsing.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# --- Enums ---


class RoutePath(str, Enum):
    """Routing paths for incoming work."""

    FULL_PROJECT = "full_project"
    RESEARCH = "research"
    SMALL_FIX = "small_fix"
    OSS_CONTRIBUTION = "oss_contribution"


class WorkType(str, Enum):
    """Type of work a task represents."""

    CODE = "code"
    RESEARCH = "research"
    PLANNING = "planning"
    ARCHITECTURE = "architecture"
    DOCUMENTATION = "documentation"


class QuestionType(str, Enum):
    """Category of a question raised during execution.

    Note: These values differ from the original Plan 3 spec (which used
    architecture, routing_policy, spec_ambiguity, technical). These are more
    general-purpose. Task 7 (escalation) routing tables should use these values.
    """

    TECHNICAL = "technical"
    ARCHITECTURAL = "architectural"
    PRODUCT = "product"
    PROCESS = "process"
    BLOCKED = "blocked"


class EscalationLevel(str, Enum):
    """Where a question gets escalated to.

    Note: Uses shortened values (supervisor/leadership/human) rather than the
    plan's escalated_to_supervisor/escalated_to_leadership/escalated_to_human.
    """

    SUPERVISOR = "supervisor"
    LEADERSHIP = "leadership"
    HUMAN = "human"


class ImplementationResult(BaseModel):
    """Result envelope for engineer implementation steps."""

    status: Literal["completed", "needs_clarification", "blocked"]
    question: str | None = Field(
        default=None,
        description="Question for supervisor if status is needs_clarification or blocked",
    )
    files_changed: list[str] = Field(
        default_factory=list,
        description="List of file paths modified during implementation",
    )
    tests_added: list[str] = Field(
        default_factory=list,
        description="List of test file paths created or modified",
    )
    summary: str = Field(min_length=1, description="What was built and why")
    confidence: Literal["high", "medium", "low"] = Field(
        description="Agent's confidence in the implementation quality",
    )

    @field_validator("files_changed", "tests_added")
    @classmethod
    def _no_empty_paths(cls, v: list[str]) -> list[str]:
        for path in v:
            if not path.strip():
                raise ValueError("File paths must not be empty strings")
        return v

    @model_validator(mode="after")
    def _question_required_when_blocked(self) -> ImplementationResult:
        if self.status in ("needs_clarification", "blocked"):
            if self.question is None or not self.question.strip():
                raise ValueError(f"'question' is required when status is '{self.status}'")
        return self


class ReviewComment(BaseModel):
    """A single review comment on a specific file location."""

    file: str = Field(min_length=1, description="Path to the file being commented on")
    line: int = Field(ge=1, le=1_000_000, description="Line number of the comment")
    severity: Literal["error", "warning", "nitpick"] = Field(
        description="Severity level of the comment",
    )
    comment: str = Field(min_length=1, description="The review comment text")


class ReviewResult(BaseModel):
    """Result envelope for peer review and validation steps."""

    verdict: Literal["approved", "approved_with_comments", "needs_revision", "blocked"]
    comments: list[ReviewComment] = Field(
        default_factory=list,
        description="List of review comments with file locations",
    )
    summary: str = Field(min_length=1, description="Overall review summary")

    @model_validator(mode="after")
    def validate_comments_for_verdict(self) -> ReviewResult:
        if self.verdict in ("needs_revision", "approved_with_comments") and not self.comments:
            raise ValueError(f"verdict '{self.verdict}' requires at least one comment")
        if self.verdict == "blocked" and not self.comments:
            raise ValueError("blocked verdict requires at least one comment explaining the blocker")
        return self


_TASK_ID_RE = re.compile(r"^T-[1-9]\d*$")


class TaskDecomposition(BaseModel):
    """A single task within a decomposition result."""

    id: str = Field(min_length=1, description="Task ID (e.g., T-1)")
    description: str = Field(min_length=1, description="What the task accomplishes")
    assigned_to: str = Field(min_length=1, description="Agent role slug (e.g., backend_engineer)")
    team: Literal["a", "b"] = Field(description="Which team owns this task")
    depends_on: list[str] = Field(
        default_factory=list,
        description="Task IDs that must complete before this task",
    )
    pr_group: str = Field(
        min_length=1,
        description="PR group name — tasks in the same group ship as one PR",
    )
    work_type: WorkType = Field(
        default=WorkType.CODE,
        description="Type of work this task represents",
    )

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _TASK_ID_RE.match(v):
            raise ValueError(f"Task ID must match T-<n> (e.g., T-1), got '{v}'")
        return v

    @field_validator("depends_on")
    @classmethod
    def _validate_depends_on(cls, v: list[str]) -> list[str]:
        for dep in v:
            if not _TASK_ID_RE.match(dep):
                raise ValueError(f"depends_on entries must match T-<n> (e.g., T-1), got '{dep}'")
        return v

    @model_validator(mode="after")
    def _no_self_dependency(self) -> "TaskDecomposition":
        if self.id in self.depends_on:
            raise ValueError(f"Task {self.id} cannot depend on itself")
        return self


class DecompositionResult(BaseModel):
    """Result envelope for Chief Architect decomposition step."""

    tasks: list[TaskDecomposition] = Field(
        min_length=1,
        description="Ordered list of tasks with dependencies",
    )
    peer_assignments: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of task_id to peer reviewer role slug",
    )
    parallel_groups: list[list[str]] = Field(
        default_factory=list,
        description="Groups of task IDs that can execute simultaneously",
    )

    @model_validator(mode="after")
    def validate_task_graph(self) -> DecompositionResult:
        task_ids = {t.id for t in self.tasks}
        # Check for duplicate IDs
        if len(task_ids) != len(self.tasks):
            raise ValueError("Duplicate task IDs in decomposition")
        # Check depends_on references exist
        for task in self.tasks:
            for dep in task.depends_on:
                if dep not in task_ids:
                    raise ValueError(f"Task {task.id} depends on unknown task {dep}")
        # Check peer_assignments reference valid task IDs
        for tid in self.peer_assignments:
            if tid not in task_ids:
                raise ValueError(f"peer_assignments references unknown task {tid}")
        # Check parallel_groups reference valid task IDs
        for group in self.parallel_groups:
            for tid in group:
                if tid not in task_ids:
                    raise ValueError(f"parallel_groups references unknown task {tid}")
        # Check no task appears in multiple parallel_groups
        seen_in_groups: set[str] = set()
        for group in self.parallel_groups:
            for tid in group:
                if tid in seen_in_groups:
                    raise ValueError(f"Task {tid} appears in multiple parallel_groups")
                seen_in_groups.add(tid)
        # Check tasks in the same parallel_group don't depend on each other
        for group in self.parallel_groups:
            group_set = set(group)
            for task in self.tasks:
                if task.id in group_set:
                    for dep in task.depends_on:
                        if dep in group_set:
                            raise ValueError(
                                f"Tasks {task.id} and {dep} are in the same parallel_group "
                                f"but {task.id} depends on {dep}"
                            )
        # Detect dependency cycles via DFS
        visited: set[str] = set()
        in_stack: set[str] = set()
        adj = {t.id: list(t.depends_on) for t in self.tasks}

        def _dfs(node: str) -> None:
            if node in in_stack:
                raise ValueError(f"Dependency cycle detected involving task {node}")
            if node in visited:
                return
            in_stack.add(node)
            for dep in adj.get(node, []):
                _dfs(dep)
            in_stack.remove(node)
            visited.add(node)

        for task in self.tasks:
            _dfs(task.id)
        return self


class QuestionRecord(BaseModel):
    """A question raised during task execution (agent output contract).

    This is the structured output an agent returns when raising a question.
    Tracking fields (id, task_id, job_id, answer, resolved) live in the
    Question entity model (devteam.models.entities.Question) — the persistence
    layer, not the agent output contract.
    """

    question: str = Field(min_length=1, description="The question text")
    question_type: QuestionType = Field(description="Category of the question")
    context: str = Field(
        default="",
        description="Additional context about why this question arose",
    )
    escalation_level: EscalationLevel = Field(
        default=EscalationLevel.SUPERVISOR,
        description="Where the question should be escalated",
    )


class RoutingResult(BaseModel):
    """Result envelope for CEO routing decision."""

    path: RoutePath = Field(description="Which routing path to follow")
    reasoning: str = Field(min_length=1, description="Why this routing path was chosen")
    target_team: str | None = Field(
        default=None,
        description="For small_fix: which team to route to directly",
    )

    @model_validator(mode="after")
    def _validate_target_team(self) -> RoutingResult:
        if self.path == RoutePath.SMALL_FIX:
            if self.target_team not in ("a", "b"):
                raise ValueError("target_team must be 'a' or 'b' for small_fix routing")
        else:
            if self.target_team is not None:
                raise ValueError(f"target_team must be None for {self.path.value} routing")
        return self
