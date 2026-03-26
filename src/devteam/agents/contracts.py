"""Structured output contracts for agent invocations.

These Pydantic models define the JSON schemas that agents must conform to
when returning results. The orchestrator uses these to machine-parse agent
output without prose parsing.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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

    @model_validator(mode="after")
    def _question_required_when_blocked(self) -> ImplementationResult:
        if self.status in ("needs_clarification", "blocked") and self.question is None:
            raise ValueError(f"'question' is required when status is '{self.status}'")
        return self


class ReviewComment(BaseModel):
    """A single review comment on a specific file location."""

    file: str = Field(min_length=1, description="Path to the file being commented on")
    line: int = Field(ge=1, description="Line number of the comment")
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
        return self


class RoutingResult(BaseModel):
    """Result envelope for CEO routing decision."""

    path: Literal["full_project", "research", "small_fix", "oss_contribution"]
    reasoning: str = Field(min_length=1, description="Why this routing path was chosen")
