"""Entity models: Job, Task, Question, PRGroup.

Entity hierarchy:
  Job (W-1, W-2...)
    -> App (the repo/service/module)
      -> Task (T-1, T-2... unique within a job)
        -> Question (Q-1, Q-2... unique within a job)
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


# --- Status Enums ---


class JobStatus(str, Enum):
    """Job lifecycle states."""

    CREATED = "created"
    PLANNING = "planning"
    DECOMPOSING = "decomposing"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    PAUSED_RATE_LIMIT = "paused_rate_limit"
    FAILED = "failed"
    CANCELED = "canceled"


class TaskStatus(str, Enum):
    """Task lifecycle states."""

    QUEUED = "queued"
    ASSIGNED = "assigned"
    EXECUTING = "executing"
    WAITING_ON_REVIEW = "waiting_on_review"
    APPROVED = "approved"
    COMPLETED = "completed"
    WAITING_ON_QUESTION = "waiting_on_question"
    REVISION_REQUESTED = "revision_requested"
    WAITING_ON_CI = "waiting_on_ci"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELED = "canceled"


class QuestionStatus(str, Enum):
    """Question lifecycle states."""

    RAISED = "raised"
    ESCALATED_TO_SUPERVISOR = "escalated_to_supervisor"
    ESCALATED_TO_LEADERSHIP = "escalated_to_leadership"
    ESCALATED_TO_HUMAN = "escalated_to_human"
    RESOLVED = "resolved"


class Priority(str, Enum):
    """Job/task priority levels."""

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class PRStatus(str, Enum):
    """PR lifecycle states."""

    BRANCH_CREATED = "branch_created"
    PR_OPENED = "pr_opened"
    WAITING_ON_CI = "waiting_on_ci"
    CI_PASSED = "ci_passed"
    CI_FAILED = "ci_failed"
    FIXING = "fixing"
    READY_FOR_MERGE = "ready_for_merge"
    MERGED = "merged"
    CLEANED_UP = "cleaned_up"
    ESCALATED_TO_HUMAN = "escalated_to_human"


# --- ID Validation Patterns ---

JOB_ID_PATTERN = re.compile(r"^W-\d+$")
TASK_ID_PATTERN = re.compile(r"^T-\d+$")
QUESTION_ID_PATTERN = re.compile(r"^Q-\d+$")


# --- Entity Models ---


class Job(BaseModel):
    """A top-level unit of work (W-1, W-2, ...).

    Jobs contain one or more Apps (repos). Tasks belong to Apps
    and are assigned to specific agents.
    """

    job_id: str
    title: str
    status: JobStatus = JobStatus.CREATED
    priority: Priority = Priority.NORMAL
    spec_path: str | None = None
    plan_path: str | None = None
    apps: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, v: str) -> str:
        if not JOB_ID_PATTERN.match(v):
            raise ValueError(f"Job ID must match W-N format, got: {v}")
        return v


class Task(BaseModel):
    """A unit of work assigned to a specific agent (T-1, T-2, ...).

    Tasks are scoped to a Job and belong to an App.
    """

    task_id: str
    job_id: str
    description: str
    assigned_to: str
    app: str
    status: TaskStatus = TaskStatus.QUEUED
    depends_on: list[str] = Field(default_factory=list)
    pr_group: str | None = None
    peer_reviewer: str | None = None
    worktree_path: str | None = None
    session_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, v: str) -> str:
        if not TASK_ID_PATTERN.match(v):
            raise ValueError(f"Task ID must match T-N format, got: {v}")
        return v

    @property
    def display_id(self) -> str:
        """Full display ID: W-1/T-1."""
        return f"{self.job_id}/{self.task_id}"


class Question(BaseModel):
    """A question raised by an agent that pauses task execution (Q-1, Q-2, ...).

    Questions escalate up the chain: EM -> CA/CEO -> Human.
    """

    question_id: str
    job_id: str
    task_id: str
    question: str
    raised_by: str
    status: QuestionStatus = QuestionStatus.RAISED
    answer: str | None = None
    answered_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None

    @field_validator("question_id")
    @classmethod
    def validate_question_id(cls, v: str) -> str:
        if not QUESTION_ID_PATTERN.match(v):
            raise ValueError(f"Question ID must match Q-N format, got: {v}")
        return v

    @property
    def display_id(self) -> str:
        """Full display ID: W-1/Q-1."""
        return f"{self.job_id}/{self.question_id}"


class PRGroup(BaseModel):
    """A group of tasks that ship together as a single PR.

    PR Groups are an operational concept defined during CA decomposition.
    """

    branch_name: str
    job_id: str
    app: str
    task_ids: list[str]
    status: PRStatus = PRStatus.BRANCH_CREATED
    pr_number: int | None = None
    pr_url: str | None = None
    worktree_path: str | None = None
    fix_iterations: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("task_ids")
    @classmethod
    def validate_task_ids_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("PRGroup must contain at least one task")
        return v
