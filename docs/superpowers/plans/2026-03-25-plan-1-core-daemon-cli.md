# Plan 1: Core Daemon & CLI Implementation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build the foundational daemon process, CLI interface, and entity models that all other subsystems build on.

**Architecture:** A FastAPI daemon process runs on localhost:7432 with singleton PID-file locking. The Typer CLI communicates with the daemon over HTTP (httpx). Entity models (Job, Task, Question, PRGroup) use enum-based state machines with validated transitions stored via DBOS/SQLite. Configuration is loaded from `~/.devteam/config.toml` (global) and per-project `devteam.toml` files using tomllib, with project settings overriding global defaults.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Typer, DBOS (SQLite), httpx, tomllib/tomli, Pydantic

---

### Task 1: Project Scaffolding with Pixi

**Files:**
- Create: `pixi.toml`
- Create: `pyproject.toml`
- Create: `src/devteam/__init__.py`
- Create: `src/devteam/cli/__init__.py`
- Create: `src/devteam/cli/main.py`
- Create: `src/devteam/cli/commands/__init__.py`
- Create: `src/devteam/daemon/__init__.py`
- Create: `src/devteam/daemon/server.py`
- Create: `src/devteam/daemon/process.py`
- Create: `src/devteam/models/__init__.py`
- Create: `src/devteam/models/entities.py`
- Create: `src/devteam/models/state.py`
- Create: `src/devteam/config/__init__.py`
- Create: `src/devteam/config/settings.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Initialize pixi workspace and pyproject.toml**

Run:
```bash
cd /Users/scottpeterson/xdev/claude-devteam
pixi init -c conda-forge --format pyproject
```

- [ ] **Step 2: Add Python and core dependencies**

Run:
```bash
pixi add "python>=3.11,<3.13"
pixi add --pypi "dbos>=1.3,<2" "fastapi>=0.115,<1" "uvicorn[standard]>=0.34,<1" "typer>=0.15,<1" "httpx>=0.28,<1" "pydantic>=2.10,<3" "tomli>=2.2,<3"
pixi add --pypi --feature test "pytest>=8,<9" "pytest-asyncio>=0.25,<1" "httpx>=0.28,<1"
```

- [ ] **Step 3: Create the package directory structure**

Create all `__init__.py` files and empty module files:

`src/devteam/__init__.py`:
```python
"""claude-devteam: Durable AI Development Team Orchestrator."""

__version__ = "0.1.0"
```

`src/devteam/cli/__init__.py`:
```python
```

`src/devteam/cli/main.py`:
```python
"""Typer CLI entry point for devteam."""

import typer

app = typer.Typer(
    name="devteam",
    help="Durable AI Development Team Orchestrator",
    no_args_is_help=True,
)


def main() -> None:
    app()
```

`src/devteam/cli/commands/__init__.py`:
```python
```

`src/devteam/daemon/__init__.py`:
```python
```

`src/devteam/daemon/server.py`:
```python
"""FastAPI daemon server."""
```

`src/devteam/daemon/process.py`:
```python
"""Daemon process management — start, stop, PID file."""
```

`src/devteam/models/__init__.py`:
```python
```

`src/devteam/models/entities.py`:
```python
"""Entity models: Job, Task, Question, PRGroup."""
```

`src/devteam/models/state.py`:
```python
"""State machine transitions for entity lifecycles."""
```

`src/devteam/config/__init__.py`:
```python
```

`src/devteam/config/settings.py`:
```python
"""Configuration loading from TOML files."""
```

`tests/__init__.py`:
```python
```

`tests/conftest.py`:
```python
"""Shared pytest fixtures for devteam tests."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_devteam_home(tmp_path: Path) -> Path:
    """Create a temporary ~/.devteam directory structure."""
    home = tmp_path / ".devteam"
    home.mkdir()
    (home / "logs").mkdir()
    (home / "traces").mkdir()
    (home / "exports").mkdir()
    (home / "focus").mkdir()
    (home / "agents").mkdir()
    (home / "projects").mkdir()
    (home / "knowledge").mkdir()
    return home


@pytest.fixture
def tmp_project_dir(tmp_path: Path) -> Path:
    """Create a temporary project directory with a devteam.toml."""
    project = tmp_path / "myproject"
    project.mkdir()
    return project
```

- [ ] **Step 4: Configure pyproject.toml for the package**

Ensure `pyproject.toml` contains the correct `[project]` metadata and `[project.scripts]` entry point:

```toml
[project]
name = "claude-devteam"
version = "0.1.0"
description = "Durable AI Development Team Orchestrator"
requires-python = ">=3.11"

[project.scripts]
devteam = "devteam.cli.main:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 5: Verify the scaffolding works**

Run:
```bash
pixi run python -c "import devteam; print(devteam.__version__)"
```
Expected: `0.1.0`

Run:
```bash
pixi run devteam --help
```
Expected: Shows the Typer help output with "Durable AI Development Team Orchestrator"

- [ ] **Step 6: Commit**

```bash
git add pixi.toml pyproject.toml pixi.lock src/ tests/
git commit -m "feat: scaffold devteam project with pixi, typer CLI entry point"
```

---

### Task 2: Entity Models — Job, Task, Question, PRGroup

**Files:**
- Create: `src/devteam/models/entities.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
"""Tests for entity models."""

from datetime import datetime, timezone

import pytest

from devteam.models.entities import (
    Job,
    JobStatus,
    PRGroup,
    PRStatus,
    Question,
    QuestionStatus,
    Task,
    TaskStatus,
)


class TestJobModel:
    def test_create_job(self) -> None:
        job = Job(
            job_id="W-1",
            title="My App",
            spec_path="/path/to/spec.md",
            plan_path="/path/to/plan.md",
        )
        assert job.job_id == "W-1"
        assert job.status == JobStatus.CREATED
        assert job.title == "My App"
        assert isinstance(job.created_at, datetime)

    def test_job_id_format(self) -> None:
        with pytest.raises(ValueError):
            Job(job_id="invalid", title="Bad ID")

    def test_job_default_priority(self) -> None:
        job = Job(job_id="W-1", title="Test")
        assert job.priority == "normal"

    def test_job_tracks_apps(self) -> None:
        job = Job(job_id="W-1", title="Test", apps=["api-service", "frontend"])
        assert len(job.apps) == 2


class TestTaskModel:
    def test_create_task(self) -> None:
        task = Task(
            task_id="T-1",
            job_id="W-1",
            description="Build API schema",
            assigned_to="backend",
            app="api-service",
        )
        assert task.task_id == "T-1"
        assert task.status == TaskStatus.QUEUED
        assert task.assigned_to == "backend"

    def test_task_id_format(self) -> None:
        with pytest.raises(ValueError):
            Task(task_id="bad", job_id="W-1", description="x", assigned_to="backend", app="api")

    def test_task_dependencies(self) -> None:
        task = Task(
            task_id="T-3",
            job_id="W-1",
            description="CI pipeline",
            assigned_to="devops",
            app="api-service",
            depends_on=["T-1", "T-2"],
        )
        assert task.depends_on == ["T-1", "T-2"]

    def test_task_display_id(self) -> None:
        task = Task(
            task_id="T-1",
            job_id="W-1",
            description="Test",
            assigned_to="backend",
            app="api",
        )
        assert task.display_id == "W-1/T-1"

    def test_task_pr_group(self) -> None:
        task = Task(
            task_id="T-1",
            job_id="W-1",
            description="Test",
            assigned_to="backend",
            app="api",
            pr_group="feat/user-auth",
        )
        assert task.pr_group == "feat/user-auth"


class TestQuestionModel:
    def test_create_question(self) -> None:
        question = Question(
            question_id="Q-1",
            job_id="W-1",
            task_id="T-2",
            question="Redis session store or JWT?",
            raised_by="backend",
        )
        assert question.question_id == "Q-1"
        assert question.status == QuestionStatus.RAISED
        assert question.answer is None

    def test_question_id_format(self) -> None:
        with pytest.raises(ValueError):
            Question(
                question_id="bad",
                job_id="W-1",
                task_id="T-1",
                question="?",
                raised_by="backend",
            )

    def test_question_display_id(self) -> None:
        q = Question(
            question_id="Q-3",
            job_id="W-1",
            task_id="T-2",
            question="?",
            raised_by="backend",
        )
        assert q.display_id == "W-1/Q-3"


class TestPRGroupModel:
    def test_create_pr_group(self) -> None:
        pr = PRGroup(
            branch_name="feat/user-auth",
            job_id="W-1",
            app="api-service",
            task_ids=["T-1", "T-2"],
        )
        assert pr.status == PRStatus.BRANCH_CREATED
        assert pr.pr_number is None

    def test_pr_group_requires_tasks(self) -> None:
        with pytest.raises(ValueError):
            PRGroup(
                branch_name="feat/empty",
                job_id="W-1",
                app="api-service",
                task_ids=[],
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pixi run pytest tests/test_models.py -v
```
Expected: FAIL with `ImportError` — modules don't exist yet

- [ ] **Step 3: Write minimal implementation**

`src/devteam/models/entities.py`:
```python
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
from typing import Optional

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
    priority: str = "normal"
    spec_path: Optional[str] = None
    plan_path: Optional[str] = None
    apps: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, v: str) -> str:
        if not JOB_ID_PATTERN.match(v):
            raise ValueError(f"Job ID must match W-N format, got: {v}")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        if v not in ("high", "normal", "low"):
            raise ValueError(f"Priority must be high/normal/low, got: {v}")
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
    pr_group: Optional[str] = None
    peer_reviewer: Optional[str] = None
    worktree_path: Optional[str] = None
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
    answer: Optional[str] = None
    answered_by: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None

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
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    worktree_path: Optional[str] = None
    fix_iterations: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("task_ids")
    @classmethod
    def validate_task_ids_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("PRGroup must contain at least one task")
        return v
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pixi run pytest tests/test_models.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/models/entities.py tests/test_models.py
git commit -m "feat: add entity models with Pydantic validation — Job, Task, Question, PRGroup"
```

---

### Task 3: State Machine Transitions

**Files:**
- Create: `src/devteam/models/state.py`
- Test: `tests/test_state.py`

- [ ] **Step 1: Write the failing test**

`tests/test_state.py`:
```python
"""Tests for state machine transitions."""

import pytest

from devteam.models.entities import JobStatus, PRStatus, QuestionStatus, TaskStatus
from devteam.models.state import (
    InvalidTransitionError,
    validate_job_transition,
    validate_pr_transition,
    validate_question_transition,
    validate_task_transition,
)


class TestJobTransitions:
    def test_valid_forward_transition(self) -> None:
        validate_job_transition(JobStatus.CREATED, JobStatus.PLANNING)

    def test_valid_planning_to_decomposing(self) -> None:
        validate_job_transition(JobStatus.PLANNING, JobStatus.DECOMPOSING)

    def test_valid_executing_to_paused(self) -> None:
        validate_job_transition(JobStatus.EXECUTING, JobStatus.PAUSED_RATE_LIMIT)

    def test_valid_paused_to_executing(self) -> None:
        validate_job_transition(JobStatus.PAUSED_RATE_LIMIT, JobStatus.EXECUTING)

    def test_invalid_backward_transition(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_job_transition(JobStatus.COMPLETED, JobStatus.CREATED)

    def test_canceled_from_executing(self) -> None:
        validate_job_transition(JobStatus.EXECUTING, JobStatus.CANCELED)

    def test_cannot_leave_completed(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_job_transition(JobStatus.COMPLETED, JobStatus.EXECUTING)

    def test_cannot_leave_canceled(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_job_transition(JobStatus.CANCELED, JobStatus.EXECUTING)


class TestTaskTransitions:
    def test_valid_queued_to_assigned(self) -> None:
        validate_task_transition(TaskStatus.QUEUED, TaskStatus.ASSIGNED)

    def test_valid_assigned_to_executing(self) -> None:
        validate_task_transition(TaskStatus.ASSIGNED, TaskStatus.EXECUTING)

    def test_valid_executing_to_waiting_on_review(self) -> None:
        validate_task_transition(TaskStatus.EXECUTING, TaskStatus.WAITING_ON_REVIEW)

    def test_valid_revision_loop(self) -> None:
        validate_task_transition(TaskStatus.WAITING_ON_REVIEW, TaskStatus.REVISION_REQUESTED)
        validate_task_transition(TaskStatus.REVISION_REQUESTED, TaskStatus.EXECUTING)

    def test_valid_executing_to_waiting_on_question(self) -> None:
        validate_task_transition(TaskStatus.EXECUTING, TaskStatus.WAITING_ON_QUESTION)

    def test_valid_question_resolved_to_executing(self) -> None:
        validate_task_transition(TaskStatus.WAITING_ON_QUESTION, TaskStatus.EXECUTING)

    def test_canceled_from_any_nonterminal(self) -> None:
        nonterminal = [
            TaskStatus.QUEUED,
            TaskStatus.ASSIGNED,
            TaskStatus.EXECUTING,
            TaskStatus.WAITING_ON_REVIEW,
            TaskStatus.WAITING_ON_QUESTION,
            TaskStatus.WAITING_ON_CI,
            TaskStatus.PAUSED,
        ]
        for status in nonterminal:
            validate_task_transition(status, TaskStatus.CANCELED)

    def test_cannot_cancel_completed(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_task_transition(TaskStatus.COMPLETED, TaskStatus.CANCELED)

    def test_invalid_skip_transition(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_task_transition(TaskStatus.QUEUED, TaskStatus.COMPLETED)


class TestQuestionTransitions:
    def test_valid_raised_to_escalated_supervisor(self) -> None:
        validate_question_transition(QuestionStatus.RAISED, QuestionStatus.ESCALATED_TO_SUPERVISOR)

    def test_valid_raised_to_resolved(self) -> None:
        validate_question_transition(QuestionStatus.RAISED, QuestionStatus.RESOLVED)

    def test_valid_supervisor_to_leadership(self) -> None:
        validate_question_transition(
            QuestionStatus.ESCALATED_TO_SUPERVISOR, QuestionStatus.ESCALATED_TO_LEADERSHIP
        )

    def test_valid_leadership_to_human(self) -> None:
        validate_question_transition(
            QuestionStatus.ESCALATED_TO_LEADERSHIP, QuestionStatus.ESCALATED_TO_HUMAN
        )

    def test_valid_human_to_resolved(self) -> None:
        validate_question_transition(QuestionStatus.ESCALATED_TO_HUMAN, QuestionStatus.RESOLVED)

    def test_cannot_leave_resolved(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_question_transition(QuestionStatus.RESOLVED, QuestionStatus.RAISED)


class TestPRTransitions:
    def test_valid_forward_flow(self) -> None:
        validate_pr_transition(PRStatus.BRANCH_CREATED, PRStatus.PR_OPENED)
        validate_pr_transition(PRStatus.PR_OPENED, PRStatus.WAITING_ON_CI)
        validate_pr_transition(PRStatus.WAITING_ON_CI, PRStatus.CI_PASSED)
        validate_pr_transition(PRStatus.CI_PASSED, PRStatus.READY_FOR_MERGE)
        validate_pr_transition(PRStatus.READY_FOR_MERGE, PRStatus.MERGED)
        validate_pr_transition(PRStatus.MERGED, PRStatus.CLEANED_UP)

    def test_ci_failure_loop(self) -> None:
        validate_pr_transition(PRStatus.WAITING_ON_CI, PRStatus.CI_FAILED)
        validate_pr_transition(PRStatus.CI_FAILED, PRStatus.FIXING)
        validate_pr_transition(PRStatus.FIXING, PRStatus.WAITING_ON_CI)

    def test_escalation_from_fixing(self) -> None:
        validate_pr_transition(PRStatus.FIXING, PRStatus.ESCALATED_TO_HUMAN)

    def test_cannot_skip_ci(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_pr_transition(PRStatus.PR_OPENED, PRStatus.MERGED)
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pixi run pytest tests/test_state.py -v
```
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/devteam/models/state.py`:
```python
"""State machine transitions for entity lifecycles.

Each entity type has a defined set of valid transitions. The validate_*_transition
functions enforce these rules and raise InvalidTransitionError on violations.
"""

from __future__ import annotations

from devteam.models.entities import JobStatus, PRStatus, QuestionStatus, TaskStatus


class InvalidTransitionError(Exception):
    """Raised when an entity state transition is not allowed."""

    def __init__(self, entity_type: str, from_state: str, to_state: str) -> None:
        self.entity_type = entity_type
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Invalid {entity_type} transition: {from_state} -> {to_state}"
        )


# --- Job Transitions ---
# created -> planning -> decomposing -> executing -> reviewing -> completed
#                                          <-> paused_rate_limit
#                                          -> failed
#                                          -> canceled (from any non-terminal)

JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.CREATED: {JobStatus.PLANNING, JobStatus.CANCELED},
    JobStatus.PLANNING: {JobStatus.DECOMPOSING, JobStatus.FAILED, JobStatus.CANCELED},
    JobStatus.DECOMPOSING: {JobStatus.EXECUTING, JobStatus.FAILED, JobStatus.CANCELED},
    JobStatus.EXECUTING: {
        JobStatus.REVIEWING,
        JobStatus.PAUSED_RATE_LIMIT,
        JobStatus.FAILED,
        JobStatus.CANCELED,
    },
    JobStatus.REVIEWING: {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED},
    JobStatus.PAUSED_RATE_LIMIT: {JobStatus.EXECUTING, JobStatus.CANCELED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: {JobStatus.CANCELED},
    JobStatus.CANCELED: set(),
}


# --- Task Transitions ---
# queued -> assigned -> executing -> waiting_on_review -> approved -> completed
#                          <-> waiting_on_question
#                          <-> waiting_on_ci
#                          -> paused
#                     waiting_on_review <-> revision_requested -> executing
#                     canceled (from any non-terminal)

TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.ASSIGNED, TaskStatus.CANCELED},
    TaskStatus.ASSIGNED: {TaskStatus.EXECUTING, TaskStatus.CANCELED},
    TaskStatus.EXECUTING: {
        TaskStatus.WAITING_ON_REVIEW,
        TaskStatus.WAITING_ON_QUESTION,
        TaskStatus.WAITING_ON_CI,
        TaskStatus.PAUSED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
    },
    TaskStatus.WAITING_ON_REVIEW: {
        TaskStatus.APPROVED,
        TaskStatus.REVISION_REQUESTED,
        TaskStatus.CANCELED,
    },
    TaskStatus.APPROVED: {TaskStatus.COMPLETED, TaskStatus.CANCELED},
    TaskStatus.WAITING_ON_QUESTION: {TaskStatus.EXECUTING, TaskStatus.CANCELED},
    TaskStatus.REVISION_REQUESTED: {TaskStatus.EXECUTING, TaskStatus.CANCELED},
    TaskStatus.WAITING_ON_CI: {TaskStatus.EXECUTING, TaskStatus.PAUSED, TaskStatus.CANCELED},
    TaskStatus.PAUSED: {TaskStatus.EXECUTING, TaskStatus.CANCELED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: {TaskStatus.CANCELED},
    TaskStatus.CANCELED: set(),
}


# --- Question Transitions ---
# raised -> escalated_to_supervisor -> escalated_to_leadership -> escalated_to_human -> resolved
# (resolved can also be reached from raised or any escalation level)

QUESTION_TRANSITIONS: dict[QuestionStatus, set[QuestionStatus]] = {
    QuestionStatus.RAISED: {
        QuestionStatus.ESCALATED_TO_SUPERVISOR,
        QuestionStatus.RESOLVED,
    },
    QuestionStatus.ESCALATED_TO_SUPERVISOR: {
        QuestionStatus.ESCALATED_TO_LEADERSHIP,
        QuestionStatus.RESOLVED,
    },
    QuestionStatus.ESCALATED_TO_LEADERSHIP: {
        QuestionStatus.ESCALATED_TO_HUMAN,
        QuestionStatus.RESOLVED,
    },
    QuestionStatus.ESCALATED_TO_HUMAN: {
        QuestionStatus.RESOLVED,
    },
    QuestionStatus.RESOLVED: set(),
}


# --- PR Transitions ---
# branch_created -> pr_opened -> waiting_on_ci -> ci_passed -> ready_for_merge -> merged -> cleaned_up
#                                 waiting_on_ci -> ci_failed -> fixing -> waiting_on_ci
#                                                               fixing -> escalated_to_human

PR_TRANSITIONS: dict[PRStatus, set[PRStatus]] = {
    PRStatus.BRANCH_CREATED: {PRStatus.PR_OPENED},
    PRStatus.PR_OPENED: {PRStatus.WAITING_ON_CI},
    PRStatus.WAITING_ON_CI: {PRStatus.CI_PASSED, PRStatus.CI_FAILED},
    PRStatus.CI_PASSED: {PRStatus.READY_FOR_MERGE},
    PRStatus.CI_FAILED: {PRStatus.FIXING},
    PRStatus.FIXING: {PRStatus.WAITING_ON_CI, PRStatus.ESCALATED_TO_HUMAN},
    PRStatus.READY_FOR_MERGE: {PRStatus.MERGED},
    PRStatus.MERGED: {PRStatus.CLEANED_UP},
    PRStatus.CLEANED_UP: set(),
    PRStatus.ESCALATED_TO_HUMAN: set(),
}


def _validate_transition(
    entity_type: str,
    transitions: dict,
    from_state: str,
    to_state: str,
) -> None:
    """Generic transition validator."""
    valid_targets = transitions.get(from_state, set())
    if to_state not in valid_targets:
        raise InvalidTransitionError(entity_type, from_state.value, to_state.value)


def validate_job_transition(from_state: JobStatus, to_state: JobStatus) -> None:
    """Validate a Job state transition."""
    _validate_transition("Job", JOB_TRANSITIONS, from_state, to_state)


def validate_task_transition(from_state: TaskStatus, to_state: TaskStatus) -> None:
    """Validate a Task state transition."""
    _validate_transition("Task", TASK_TRANSITIONS, from_state, to_state)


def validate_question_transition(
    from_state: QuestionStatus, to_state: QuestionStatus
) -> None:
    """Validate a Question state transition."""
    _validate_transition("Question", QUESTION_TRANSITIONS, from_state, to_state)


def validate_pr_transition(from_state: PRStatus, to_state: PRStatus) -> None:
    """Validate a PR state transition."""
    _validate_transition("PR", PR_TRANSITIONS, from_state, to_state)
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pixi run pytest tests/test_state.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/models/state.py tests/test_state.py
git commit -m "feat: add state machine transitions for Job, Task, Question, PR lifecycles"
```

---

### Task 4: Configuration Loading

**Files:**
- Create: `src/devteam/config/settings.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
"""Tests for configuration loading."""

from pathlib import Path

import pytest

from devteam.config.settings import (
    ApprovalConfig,
    DaemonConfig,
    DevteamConfig,
    GeneralConfig,
    GitConfig,
    PRConfig,
    ProjectConfig,
    RateLimitConfig,
    load_global_config,
    load_project_config,
    merge_configs,
)


class TestDefaultConfig:
    def test_default_daemon_config(self) -> None:
        config = DevteamConfig()
        assert config.daemon.port == 7432

    def test_default_general_config(self) -> None:
        config = DevteamConfig()
        assert config.general.max_concurrent_agents == 3

    def test_default_approval_config(self) -> None:
        config = DevteamConfig()
        assert config.approval.commit == "auto"
        assert config.approval.push_to_main == "never"

    def test_default_rate_limit_config(self) -> None:
        config = DevteamConfig()
        assert config.rate_limit.default_backoff_seconds == 1800

    def test_default_pr_config(self) -> None:
        config = DevteamConfig()
        assert config.pr.max_fix_iterations == 5
        assert config.pr.ci_poll_interval_seconds == 60

    def test_default_git_config(self) -> None:
        config = DevteamConfig()
        assert config.git.worktree_dir == ".worktrees"


class TestLoadGlobalConfig:
    def test_load_from_toml_file(self, tmp_devteam_home: Path) -> None:
        config_path = tmp_devteam_home / "config.toml"
        config_path.write_text(
            """\
[daemon]
port = 8080

[general]
max_concurrent_agents = 5

[approval]
merge = "manual"
"""
        )
        config = load_global_config(config_path)
        assert config.daemon.port == 8080
        assert config.general.max_concurrent_agents == 5
        assert config.approval.merge == "manual"
        # Defaults preserved for unspecified values
        assert config.approval.commit == "auto"
        assert config.approval.push_to_main == "never"

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        config = load_global_config(tmp_path / "nonexistent.toml")
        assert config.daemon.port == 7432
        assert config.general.max_concurrent_agents == 3

    def test_load_empty_file_returns_defaults(self, tmp_devteam_home: Path) -> None:
        config_path = tmp_devteam_home / "config.toml"
        config_path.write_text("")
        config = load_global_config(config_path)
        assert config.daemon.port == 7432


class TestLoadProjectConfig:
    def test_load_project_config(self, tmp_project_dir: Path) -> None:
        config_path = tmp_project_dir / "devteam.toml"
        config_path.write_text(
            """\
[project]
name = "myapp"
repos = ["github.com/user/myapp-api", "github.com/user/myapp-ui"]

[approval]
merge = "manual"

[execution]
test_command = "npm test"
lint_command = "npm run lint"
build_command = "npm run build"
merge_strategy = "squash"
"""
        )
        config = load_project_config(config_path)
        assert config.project.name == "myapp"
        assert len(config.project.repos) == 2
        assert config.approval.merge == "manual"
        assert config.execution.test_command == "npm test"

    def test_load_missing_project_config_returns_none(self, tmp_path: Path) -> None:
        config = load_project_config(tmp_path / "nonexistent.toml")
        assert config is None


class TestMergeConfigs:
    def test_project_overrides_global(self, tmp_devteam_home: Path, tmp_project_dir: Path) -> None:
        global_path = tmp_devteam_home / "config.toml"
        global_path.write_text(
            """\
[approval]
merge = "auto"
commit = "auto"
"""
        )
        project_path = tmp_project_dir / "devteam.toml"
        project_path.write_text(
            """\
[project]
name = "myapp"

[approval]
merge = "manual"
"""
        )
        global_config = load_global_config(global_path)
        project_config = load_project_config(project_path)
        merged = merge_configs(global_config, project_config)

        assert merged.approval.merge == "manual"  # overridden
        assert merged.approval.commit == "auto"  # preserved from global

    def test_merge_with_no_project_config(self, tmp_devteam_home: Path) -> None:
        global_path = tmp_devteam_home / "config.toml"
        global_path.write_text("[daemon]\nport = 9999\n")
        global_config = load_global_config(global_path)
        merged = merge_configs(global_config, None)
        assert merged.daemon.port == 9999


class TestPushToMainNeverOverridable:
    def test_push_to_main_stays_never(self, tmp_devteam_home: Path) -> None:
        """push_to_main = 'never' is a hard block that cannot be overridden."""
        config_path = tmp_devteam_home / "config.toml"
        config_path.write_text(
            """\
[approval]
push_to_main = "auto"
"""
        )
        config = load_global_config(config_path)
        # push_to_main is always forced to "never" regardless of config
        assert config.approval.push_to_main == "never"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pixi run pytest tests/test_config.py -v
```
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/devteam/config/settings.py`:
```python
"""Configuration loading from TOML files.

Global config: ~/.devteam/config.toml
Project config: <project-root>/devteam.toml

Project settings override global settings where applicable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


# --- Config Section Models ---


class DaemonConfig(BaseModel):
    """Daemon process configuration."""

    port: int = 7432


class GeneralConfig(BaseModel):
    """General operational settings."""

    max_concurrent_agents: int = 3


class ModelsConfig(BaseModel):
    """Model tier assignments."""

    executive: str = "opus"
    engineering: str = "sonnet"
    validation: str = "haiku"
    extraction: str = "haiku"


class ApprovalConfig(BaseModel):
    """Approval policy configuration.

    push_to_main is a hard block — always "never", cannot be overridden.
    """

    commit: str = "auto"
    push: str = "auto"
    open_pr: str = "auto"
    merge: str = "auto"
    cleanup: str = "auto"
    push_to_main: str = "never"

    @model_validator(mode="after")
    def enforce_push_to_main_never(self) -> "ApprovalConfig":
        """push_to_main = 'never' is a hard safety block."""
        self.push_to_main = "never"
        return self


class KnowledgeConfig(BaseModel):
    """Knowledge system configuration."""

    embedding_model: str = "nomic-embed-text"
    surrealdb_path: str = "file://~/.devteam/knowledge"
    cross_project_sharing: str = "layered"


class RateLimitConfig(BaseModel):
    """Rate limit handling configuration."""

    default_backoff_seconds: int = 1800


class PRConfig(BaseModel):
    """PR lifecycle configuration."""

    max_fix_iterations: int = 5
    ci_poll_interval_seconds: int = 60


class GitConfig(BaseModel):
    """Git operations configuration."""

    worktree_dir: str = ".worktrees"


class ProjectInfo(BaseModel):
    """Per-project metadata."""

    name: str = ""
    repos: list[str] = Field(default_factory=list)


class ExecutionConfig(BaseModel):
    """Per-project execution commands."""

    test_command: Optional[str] = None
    lint_command: Optional[str] = None
    build_command: Optional[str] = None
    merge_strategy: str = "squash"
    pr_template: Optional[str] = None


# --- Top-Level Config Models ---


class DevteamConfig(BaseModel):
    """Complete devteam configuration (global)."""

    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    pr: PRConfig = Field(default_factory=PRConfig)
    git: GitConfig = Field(default_factory=GitConfig)


class ProjectDevteamConfig(BaseModel):
    """Per-project devteam.toml configuration."""

    project: ProjectInfo = Field(default_factory=ProjectInfo)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)


# --- Loading Functions ---


def load_global_config(config_path: Path) -> DevteamConfig:
    """Load global configuration from ~/.devteam/config.toml.

    Returns defaults if the file does not exist or is empty.
    """
    if not config_path.exists():
        return DevteamConfig()

    text = config_path.read_text()
    if not text.strip():
        return DevteamConfig()

    data = tomllib.loads(text)
    return DevteamConfig(**data)


def load_project_config(config_path: Path) -> Optional[ProjectDevteamConfig]:
    """Load per-project configuration from devteam.toml.

    Returns None if the file does not exist.
    """
    if not config_path.exists():
        return None

    text = config_path.read_text()
    if not text.strip():
        return ProjectDevteamConfig()

    data = tomllib.loads(text)
    return ProjectDevteamConfig(**data)


def merge_configs(
    global_config: DevteamConfig,
    project_config: Optional[ProjectDevteamConfig],
) -> DevteamConfig:
    """Merge project config into global config.

    Project-level approval settings override global approval settings.
    All other global settings are preserved.
    """
    if project_config is None:
        return global_config

    merged_data = global_config.model_dump()

    # Merge approval overrides from project
    project_approval = project_config.approval.model_dump(exclude_defaults=True)
    for key, value in project_approval.items():
        merged_data["approval"][key] = value

    return DevteamConfig(**merged_data)
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pixi run pytest tests/test_config.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/config/settings.py tests/test_config.py
git commit -m "feat: add TOML config loading with global/project merge and safety enforcement"
```

---

### Task 5: Daemon Process Management (PID file, start/stop)

**Files:**
- Create: `src/devteam/daemon/process.py`
- Test: `tests/test_daemon.py`

- [ ] **Step 1: Write the failing test**

`tests/test_daemon.py`:
```python
"""Tests for daemon process management."""

import os
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devteam.daemon.process import (
    DaemonAlreadyRunningError,
    DaemonNotRunningError,
    DaemonState,
    acquire_pid_lock,
    get_daemon_state,
    read_pid_file,
    release_pid_lock,
    write_pid_file,
)


class TestPIDFile:
    def test_write_and_read_pid_file(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        write_pid_file(pid_path, 12345)
        assert read_pid_file(pid_path) == 12345

    def test_read_missing_pid_file(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        assert read_pid_file(pid_path) is None

    def test_read_corrupt_pid_file(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        pid_path.write_text("not-a-number\n")
        assert read_pid_file(pid_path) is None


class TestPIDLock:
    def test_acquire_lock_succeeds(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        acquire_pid_lock(pid_path, os.getpid())
        assert read_pid_file(pid_path) == os.getpid()

    def test_acquire_lock_fails_if_running(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        # Write our own PID (which is a running process)
        write_pid_file(pid_path, os.getpid())
        with pytest.raises(DaemonAlreadyRunningError):
            acquire_pid_lock(pid_path, 99999)

    def test_acquire_lock_succeeds_if_stale(self, tmp_devteam_home: Path) -> None:
        """If the PID in the file is not a running process, the lock is stale."""
        pid_path = tmp_devteam_home / "daemon.pid"
        # Use a PID that almost certainly doesn't exist
        write_pid_file(pid_path, 4_000_000)
        acquire_pid_lock(pid_path, os.getpid())
        assert read_pid_file(pid_path) == os.getpid()

    def test_release_lock(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        write_pid_file(pid_path, os.getpid())
        release_pid_lock(pid_path)
        assert not pid_path.exists()

    def test_release_missing_lock_is_noop(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        release_pid_lock(pid_path)  # Should not raise


class TestDaemonState:
    def test_state_stopped_when_no_pid(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        port_path = tmp_devteam_home / "daemon.port"
        state = get_daemon_state(pid_path, port_path)
        assert state.running is False
        assert state.pid is None

    def test_state_running_with_pid(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        port_path = tmp_devteam_home / "daemon.port"
        write_pid_file(pid_path, os.getpid())
        port_path.write_text("7432\n")
        state = get_daemon_state(pid_path, port_path)
        assert state.running is True
        assert state.pid == os.getpid()
        assert state.port == 7432

    def test_state_stale_pid(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        port_path = tmp_devteam_home / "daemon.port"
        write_pid_file(pid_path, 4_000_000)
        state = get_daemon_state(pid_path, port_path)
        assert state.running is False
        assert state.stale is True
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pixi run pytest tests/test_daemon.py -v
```
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/devteam/daemon/process.py`:
```python
"""Daemon process management -- start, stop, PID file, singleton lock.

The devteam daemon is a single long-running process. A PID file at
~/.devteam/daemon.pid provides singleton locking. A port file at
~/.devteam/daemon.port records the active listening port.
"""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class DaemonAlreadyRunningError(Exception):
    """Raised when trying to start a daemon that is already running."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        super().__init__(f"Daemon already running with PID {pid}")


class DaemonNotRunningError(Exception):
    """Raised when a command requires a running daemon but none is found."""

    def __init__(self) -> None:
        super().__init__("Daemon is not running. Start it with: devteam daemon start")


@dataclass
class DaemonState:
    """Current state of the daemon process."""

    running: bool
    pid: Optional[int] = None
    port: Optional[int] = None
    stale: bool = False


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def write_pid_file(pid_path: Path, pid: int) -> None:
    """Write the daemon PID to the PID file."""
    pid_path.write_text(f"{pid}\n")


def read_pid_file(pid_path: Path) -> Optional[int]:
    """Read the daemon PID from the PID file.

    Returns None if the file doesn't exist or contains invalid data.
    """
    if not pid_path.exists():
        return None
    try:
        text = pid_path.read_text().strip()
        return int(text)
    except (ValueError, OSError):
        return None


def acquire_pid_lock(pid_path: Path, new_pid: int) -> None:
    """Acquire the singleton daemon lock.

    If a PID file exists with a running process, raises DaemonAlreadyRunningError.
    If the PID file is stale (process not running), overwrites it.
    """
    existing_pid = read_pid_file(pid_path)
    if existing_pid is not None and _is_process_alive(existing_pid):
        raise DaemonAlreadyRunningError(existing_pid)

    write_pid_file(pid_path, new_pid)


def release_pid_lock(pid_path: Path) -> None:
    """Release the daemon lock by removing the PID file."""
    try:
        pid_path.unlink()
    except FileNotFoundError:
        pass


def write_port_file(port_path: Path, port: int) -> None:
    """Write the daemon port to the port file."""
    port_path.write_text(f"{port}\n")


def read_port_file(port_path: Path) -> Optional[int]:
    """Read the daemon port from the port file."""
    if not port_path.exists():
        return None
    try:
        return int(port_path.read_text().strip())
    except (ValueError, OSError):
        return None


def get_daemon_state(pid_path: Path, port_path: Path) -> DaemonState:
    """Get the current daemon state by inspecting PID and port files."""
    pid = read_pid_file(pid_path)

    if pid is None:
        return DaemonState(running=False)

    if not _is_process_alive(pid):
        return DaemonState(running=False, pid=pid, stale=True)

    port = read_port_file(port_path)
    return DaemonState(running=True, pid=pid, port=port)


def stop_daemon(pid_path: Path, port_path: Path, *, force: bool = False) -> int:
    """Stop the running daemon process.

    Returns the PID of the stopped process.
    Raises DaemonNotRunningError if no daemon is running.
    """
    pid = read_pid_file(pid_path)
    if pid is None or not _is_process_alive(pid):
        raise DaemonNotRunningError()

    sig = signal.SIGKILL if force else signal.SIGTERM
    os.kill(pid, sig)

    release_pid_lock(pid_path)
    try:
        port_path.unlink()
    except FileNotFoundError:
        pass

    return pid
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pixi run pytest tests/test_daemon.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/daemon/process.py tests/test_daemon.py
git commit -m "feat: add daemon PID file management with singleton locking"
```

---

### Task 6: FastAPI Daemon Server

**Files:**
- Create: `src/devteam/daemon/server.py`
- Update: `tests/test_daemon.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_daemon.py`:
```python
import httpx
import pytest
from httpx import ASGITransport

from devteam.daemon.server import create_app


class TestDaemonServer:
    @pytest.fixture
    def app(self) -> "FastAPI":
        return create_app()

    @pytest.fixture
    def client(self, app) -> httpx.AsyncClient:
        transport = ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    @pytest.mark.asyncio
    async def test_health_check(self, client: httpx.AsyncClient) -> None:
        async with client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "version" in data

    @pytest.mark.asyncio
    async def test_status_endpoint(self, client: httpx.AsyncClient) -> None:
        async with client:
            resp = await client.get("/api/v1/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "jobs" in data

    @pytest.mark.asyncio
    async def test_start_job_stub(self, client: httpx.AsyncClient) -> None:
        async with client:
            resp = await client.post(
                "/api/v1/jobs",
                json={"title": "Test Job", "spec_path": "/tmp/spec.md"},
            )
            assert resp.status_code == 501
            assert "not yet implemented" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_stop_job_stub(self, client: httpx.AsyncClient) -> None:
        async with client:
            resp = await client.post("/api/v1/jobs/W-1/stop")
            assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_pause_job_stub(self, client: httpx.AsyncClient) -> None:
        async with client:
            resp = await client.post("/api/v1/jobs/W-1/pause")
            assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_resume_job_stub(self, client: httpx.AsyncClient) -> None:
        async with client:
            resp = await client.post("/api/v1/jobs/W-1/resume")
            assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_cancel_job_stub(self, client: httpx.AsyncClient) -> None:
        async with client:
            resp = await client.post("/api/v1/jobs/W-1/cancel")
            assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_answer_question_stub(self, client: httpx.AsyncClient) -> None:
        async with client:
            resp = await client.post(
                "/api/v1/jobs/W-1/questions/Q-1/answer",
                json={"answer": "Use JWT"},
            )
            assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_focus_stub(self, client: httpx.AsyncClient) -> None:
        async with client:
            resp = await client.post(
                "/api/v1/focus",
                json={"job_id": "W-1", "shell_pid": 12345},
            )
            assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_project_add_stub(self, client: httpx.AsyncClient) -> None:
        async with client:
            resp = await client.post(
                "/api/v1/projects",
                json={"path": "/path/to/repo"},
            )
            assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_project_remove_stub(self, client: httpx.AsyncClient) -> None:
        async with client:
            resp = await client.delete("/api/v1/projects/myapp")
            assert resp.status_code == 501
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pixi run pytest tests/test_daemon.py::TestDaemonServer -v
```
Expected: FAIL with `ImportError` for `create_app`

- [ ] **Step 3: Write minimal implementation**

`src/devteam/daemon/server.py`:
```python
"""FastAPI daemon server.

The devteam daemon runs on localhost:7432 and provides the HTTP API
that the CLI communicates with. All job orchestration, state management,
and agent invocation flows through this server.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import devteam


def _not_implemented(feature: str) -> HTTPException:
    """Return a 501 for stub endpoints."""
    return HTTPException(status_code=501, detail=f"Not yet implemented: {feature}")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="devteam daemon",
        description="Durable AI Development Team Orchestrator",
        version=devteam.__version__,
    )

    # --- Health ---

    @app.get("/health")
    async def health_check() -> dict:
        return {"status": "ok", "version": devteam.__version__}

    # --- Status ---

    @app.get("/api/v1/status")
    async def get_status() -> dict:
        """Return overall daemon status and active jobs."""
        return {"jobs": [], "agents_running": 0, "rate_limited": False}

    # --- Job Management (stubs) ---

    class StartJobRequest(BaseModel):
        title: str
        spec_path: str | None = None
        plan_path: str | None = None
        prompt: str | None = None
        issue_url: str | None = None
        priority: str = "normal"

    @app.post("/api/v1/jobs")
    async def start_job(request: StartJobRequest) -> dict:
        raise _not_implemented("job creation and workflow execution")

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        raise _not_implemented("job detail retrieval")

    @app.post("/api/v1/jobs/{job_id}/stop")
    async def stop_job(job_id: str, force: bool = False) -> dict:
        raise _not_implemented("job stop")

    @app.post("/api/v1/jobs/{job_id}/pause")
    async def pause_job(job_id: str) -> dict:
        raise _not_implemented("job pause")

    @app.post("/api/v1/jobs/{job_id}/resume")
    async def resume_job(job_id: str) -> dict:
        raise _not_implemented("job resume")

    @app.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, revert_merged: bool = False) -> dict:
        raise _not_implemented("job cancellation")

    # --- Questions (stubs) ---

    class AnswerRequest(BaseModel):
        answer: str

    @app.post("/api/v1/jobs/{job_id}/questions/{question_id}/answer")
    async def answer_question(job_id: str, question_id: str, request: AnswerRequest) -> dict:
        raise _not_implemented("question answering")

    # --- Focus (stubs) ---

    class FocusRequest(BaseModel):
        job_id: str
        shell_pid: int

    @app.post("/api/v1/focus")
    async def set_focus(request: FocusRequest) -> dict:
        raise _not_implemented("focus management")

    # --- Project Management (stubs) ---

    class ProjectAddRequest(BaseModel):
        path: str

    @app.post("/api/v1/projects")
    async def add_project(request: ProjectAddRequest) -> dict:
        raise _not_implemented("project registration")

    @app.delete("/api/v1/projects/{project_name}")
    async def remove_project(project_name: str) -> dict:
        raise _not_implemented("project removal")

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pixi run pytest tests/test_daemon.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/daemon/server.py tests/test_daemon.py
git commit -m "feat: add FastAPI daemon server with health, status, and stub endpoints"
```

---

### Task 7: CLI Commands — init, daemon, project

**Files:**
- Create: `src/devteam/cli/commands/init_cmd.py`
- Create: `src/devteam/cli/commands/daemon_cmd.py`
- Create: `src/devteam/cli/commands/project_cmd.py`
- Update: `src/devteam/cli/main.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
"""Tests for the CLI interface."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from devteam.cli.main import app

runner = CliRunner()


class TestCLIHelp:
    def test_main_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "devteam" in result.output.lower() or "Durable" in result.output

    def test_init_help(self) -> None:
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0

    def test_daemon_help(self) -> None:
        result = runner.invoke(app, ["daemon", "--help"])
        assert result.exit_code == 0

    def test_project_help(self) -> None:
        result = runner.invoke(app, ["project", "--help"])
        assert result.exit_code == 0


class TestInitCommand:
    def test_init_creates_directory_structure(self, tmp_path: Path) -> None:
        devteam_home = tmp_path / ".devteam"
        with patch("devteam.cli.commands.init_cmd.get_devteam_home", return_value=devteam_home):
            result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert devteam_home.exists()
        assert (devteam_home / "config.toml").exists()
        assert (devteam_home / "logs").is_dir()
        assert (devteam_home / "traces").is_dir()
        assert (devteam_home / "exports").is_dir()
        assert (devteam_home / "focus").is_dir()
        assert (devteam_home / "agents").is_dir()
        assert (devteam_home / "projects").is_dir()
        assert (devteam_home / "knowledge").is_dir()

    def test_init_idempotent(self, tmp_path: Path) -> None:
        devteam_home = tmp_path / ".devteam"
        with patch("devteam.cli.commands.init_cmd.get_devteam_home", return_value=devteam_home):
            runner.invoke(app, ["init"])
            result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "already" in result.output.lower() or result.exit_code == 0


class TestDaemonCommands:
    def test_daemon_status_not_running(self, tmp_path: Path) -> None:
        devteam_home = tmp_path / ".devteam"
        devteam_home.mkdir()
        with patch("devteam.cli.commands.daemon_cmd.get_devteam_home", return_value=devteam_home):
            result = runner.invoke(app, ["daemon", "status"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()

    def test_daemon_stop_not_running(self, tmp_path: Path) -> None:
        devteam_home = tmp_path / ".devteam"
        devteam_home.mkdir()
        with patch("devteam.cli.commands.daemon_cmd.get_devteam_home", return_value=devteam_home):
            result = runner.invoke(app, ["daemon", "stop"])
        assert result.exit_code == 1


class TestProjectCommands:
    def test_project_add_stub(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["project", "add", str(tmp_path)])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_project_remove_stub(self) -> None:
        result = runner.invoke(app, ["project", "remove", "myapp"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pixi run pytest tests/test_cli.py -v
```
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/devteam/cli/commands/init_cmd.py`:
```python
"""devteam init — first-time setup."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Initialize devteam.")


DEFAULT_CONFIG = """\
# claude-devteam global configuration
# See: https://github.com/smpnet74/claude-devteam

[daemon]
port = 7432

[general]
max_concurrent_agents = 3

[models]
executive = "opus"
engineering = "sonnet"
validation = "haiku"
extraction = "haiku"

[approval]
commit = "auto"
push = "auto"
open_pr = "auto"
merge = "auto"
cleanup = "auto"
push_to_main = "never"

[knowledge]
embedding_model = "nomic-embed-text"
surrealdb_path = "file://~/.devteam/knowledge"
cross_project_sharing = "layered"

[rate_limit]
default_backoff_seconds = 1800

[pr]
max_fix_iterations = 5
ci_poll_interval_seconds = 60

[git]
worktree_dir = ".worktrees"
"""

DIRS = ["logs", "traces", "exports", "focus", "agents", "projects", "knowledge"]


def get_devteam_home() -> Path:
    """Return the devteam home directory path."""
    return Path.home() / ".devteam"


def init_devteam_home(home: Path) -> bool:
    """Create the ~/.devteam directory structure.

    Returns True if newly created, False if already existed.
    """
    created = not home.exists()
    home.mkdir(exist_ok=True)

    for d in DIRS:
        (home / d).mkdir(exist_ok=True)

    config_path = home / "config.toml"
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG)

    return created


@app.callback(invoke_without_command=True)
def init() -> None:
    """Initialize devteam — creates ~/.devteam/ and default configuration."""
    home = get_devteam_home()
    created = init_devteam_home(home)
    if created:
        typer.echo(f"Initialized devteam at {home}")
    else:
        typer.echo(f"Already initialized at {home}")
```

`src/devteam/cli/commands/daemon_cmd.py`:
```python
"""devteam daemon — start/stop/status for the daemon process."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer

from devteam.daemon.process import (
    DaemonNotRunningError,
    get_daemon_state,
    stop_daemon,
)

app = typer.Typer(help="Manage the devteam daemon process.")


def get_devteam_home() -> Path:
    """Return the devteam home directory path."""
    return Path.home() / ".devteam"


@app.command()
def start(
    port: int = typer.Option(7432, help="Port to listen on"),
    foreground: bool = typer.Option(False, "--foreground", "-f", help="Run in foreground"),
) -> None:
    """Start the devteam daemon."""
    home = get_devteam_home()
    pid_path = home / "daemon.pid"
    port_path = home / "daemon.port"

    state = get_daemon_state(pid_path, port_path)
    if state.running:
        typer.echo(f"Daemon already running (PID {state.pid}, port {state.port})")
        raise typer.Exit(code=0)

    if foreground:
        # Run in foreground — import and run directly
        from devteam.daemon.process import acquire_pid_lock, release_pid_lock, write_port_file
        from devteam.daemon.server import create_app

        import uvicorn

        acquire_pid_lock(pid_path, os.getpid())
        write_port_file(port_path, port)
        typer.echo(f"Starting daemon on port {port} (foreground, PID {os.getpid()})")
        try:
            app_instance = create_app()
            uvicorn.run(app_instance, host="127.0.0.1", port=port, log_level="warning")
        finally:
            release_pid_lock(pid_path)
            try:
                port_path.unlink()
            except FileNotFoundError:
                pass
    else:
        # Spawn as background process
        cmd = [
            sys.executable, "-m", "devteam.cli.commands.daemon_cmd",
            "--port", str(port),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        typer.echo(f"Daemon starting (PID {proc.pid}, port {port})")


@app.command()
def stop(
    force: bool = typer.Option(False, "--force", help="Force kill"),
) -> None:
    """Stop the devteam daemon."""
    home = get_devteam_home()
    pid_path = home / "daemon.pid"
    port_path = home / "daemon.port"

    try:
        pid = stop_daemon(pid_path, port_path, force=force)
        typer.echo(f"Daemon stopped (PID {pid})")
    except DaemonNotRunningError:
        typer.echo("Daemon is not running.")
        raise typer.Exit(code=1)


@app.command()
def status() -> None:
    """Show daemon status."""
    home = get_devteam_home()
    pid_path = home / "daemon.pid"
    port_path = home / "daemon.port"

    state = get_daemon_state(pid_path, port_path)

    if state.running:
        typer.echo(f"Daemon is running (PID {state.pid}, port {state.port})")
    elif state.stale:
        typer.echo(f"Daemon is not running (stale PID file: {state.pid})")
    else:
        typer.echo("Daemon is not running.")
```

`src/devteam/cli/commands/project_cmd.py`:
```python
"""devteam project — add/remove project registrations."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Manage registered projects.")


@app.command()
def add(
    path: str = typer.Argument(help="Path to the project repository"),
) -> None:
    """Register a project repository with the daemon."""
    typer.echo(f"Not yet implemented: project add ({path})")


@app.command()
def remove(
    name: str = typer.Argument(help="Project name to unregister"),
) -> None:
    """Unregister a project from the daemon."""
    typer.echo(f"Not yet implemented: project remove ({name})")
```

Update `src/devteam/cli/main.py`:
```python
"""Typer CLI entry point for devteam."""

import typer

from devteam.cli.commands import daemon_cmd, init_cmd, project_cmd

app = typer.Typer(
    name="devteam",
    help="Durable AI Development Team Orchestrator",
    no_args_is_help=True,
)

# Register command groups
app.add_typer(init_cmd.app, name="init")
app.add_typer(daemon_cmd.app, name="daemon")
app.add_typer(project_cmd.app, name="project")


def main() -> None:
    app()
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pixi run pytest tests/test_cli.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/cli/ tests/test_cli.py
git commit -m "feat: add CLI commands — init, daemon start/stop/status, project add/remove"
```

---

### Task 8: CLI Commands — Job Control Stubs (start, status, stop, pause, resume, cancel, focus)

**Files:**
- Create: `src/devteam/cli/commands/job_cmd.py`
- Create: `src/devteam/cli/commands/focus_cmd.py`
- Update: `src/devteam/cli/main.py`
- Update: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:
```python
class TestJobCommands:
    def test_start_help(self) -> None:
        result = runner.invoke(app, ["start", "--help"])
        assert result.exit_code == 0
        assert "spec" in result.output.lower() or "plan" in result.output.lower()

    def test_start_stub(self) -> None:
        result = runner.invoke(app, ["start", "--spec", "/tmp/spec.md", "--plan", "/tmp/plan.md"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_start_with_prompt(self) -> None:
        result = runner.invoke(app, ["start", "--prompt", "Fix the bug"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_start_with_issue(self) -> None:
        result = runner.invoke(app, ["start", "--issue", "https://github.com/org/repo/issues/42"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_status_stub(self) -> None:
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0

    def test_status_with_job_id(self) -> None:
        result = runner.invoke(app, ["status", "W-1"])
        assert result.exit_code == 0

    def test_status_with_task_id(self) -> None:
        result = runner.invoke(app, ["status", "W-1/T-3"])
        assert result.exit_code == 0

    def test_status_questions_flag(self) -> None:
        result = runner.invoke(app, ["status", "--questions"])
        assert result.exit_code == 0

    def test_stop_stub(self) -> None:
        result = runner.invoke(app, ["stop"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_stop_with_job_id(self) -> None:
        result = runner.invoke(app, ["stop", "W-1"])
        assert result.exit_code == 0

    def test_pause_stub(self) -> None:
        result = runner.invoke(app, ["pause", "W-1"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_resume_stub(self) -> None:
        result = runner.invoke(app, ["resume", "W-1"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_cancel_stub(self) -> None:
        result = runner.invoke(app, ["cancel", "W-1"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_cancel_revert_merged(self) -> None:
        result = runner.invoke(app, ["cancel", "W-1", "--revert-merged"])
        assert result.exit_code == 0


class TestFocusCommand:
    def test_focus_set(self) -> None:
        result = runner.invoke(app, ["focus", "W-1"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_focus_clear(self) -> None:
        result = runner.invoke(app, ["focus", "--clear"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower()

    def test_focus_show(self) -> None:
        result = runner.invoke(app, ["focus"])
        assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pixi run pytest tests/test_cli.py::TestJobCommands -v
pixi run pytest tests/test_cli.py::TestFocusCommand -v
```
Expected: FAIL — commands not registered

- [ ] **Step 3: Write minimal implementation**

`src/devteam/cli/commands/job_cmd.py`:
```python
"""devteam job control commands — start, status, stop, pause, resume, cancel.

These are registered as top-level commands (not under a subgroup) because
they are the primary operator interface.
"""

from __future__ import annotations

from typing import Optional

import typer


def register_job_commands(app: typer.Typer) -> None:
    """Register job control commands directly on the main app."""

    @app.command()
    def start(
        spec: Optional[str] = typer.Option(None, "--spec", help="Path to spec document"),
        plan: Optional[str] = typer.Option(None, "--plan", help="Path to plan document"),
        prompt: Optional[str] = typer.Option(None, "--prompt", help="Direct prompt for small fixes"),
        issue: Optional[str] = typer.Option(None, "--issue", help="GitHub issue URL"),
        priority: str = typer.Option("normal", "--priority", help="Job priority: high/normal/low"),
    ) -> None:
        """Start a new development job."""
        if spec:
            typer.echo(f"Not yet implemented: start job from spec ({spec})")
        elif prompt:
            typer.echo(f"Not yet implemented: start job from prompt")
        elif issue:
            typer.echo(f"Not yet implemented: start job from issue ({issue})")
        else:
            typer.echo("Provide --spec/--plan, --prompt, or --issue to start a job.")
            raise typer.Exit(code=1)

    @app.command()
    def status(
        target: Optional[str] = typer.Argument(None, help="Job ID (W-1), task (W-1/T-3), or omit for all"),
        questions: bool = typer.Option(False, "--questions", help="Show pending questions"),
    ) -> None:
        """Show status of active jobs and tasks."""
        if target:
            typer.echo(f"Not yet implemented: status for {target}")
        elif questions:
            typer.echo("Not yet implemented: pending questions")
        else:
            typer.echo("No active jobs.")

    @app.command()
    def stop(
        target: Optional[str] = typer.Argument(None, help="Job ID (W-1) or omit for all"),
        force: bool = typer.Option(False, "--force", help="Force kill all agents"),
    ) -> None:
        """Stop active jobs gracefully."""
        if target:
            typer.echo(f"Not yet implemented: stop job {target}")
        else:
            typer.echo("Not yet implemented: stop all jobs")

    @app.command()
    def pause(
        target: str = typer.Argument(help="Job ID (W-1)"),
    ) -> None:
        """Pause a running job."""
        typer.echo(f"Not yet implemented: pause {target}")

    @app.command()
    def resume(
        target: str = typer.Argument(help="Job ID (W-1) or omit to resume daemon"),
    ) -> None:
        """Resume a paused job or recover workflows after crash."""
        typer.echo(f"Not yet implemented: resume {target}")

    @app.command()
    def cancel(
        target: str = typer.Argument(help="Job ID (W-1)"),
        revert_merged: bool = typer.Option(False, "--revert-merged", help="Create revert PRs for merged work"),
    ) -> None:
        """Cancel a job and clean up all resources."""
        if revert_merged:
            typer.echo(f"Not yet implemented: cancel {target} with revert")
        else:
            typer.echo(f"Not yet implemented: cancel {target}")
```

`src/devteam/cli/commands/focus_cmd.py`:
```python
"""devteam focus — set/clear the focused job for the current shell."""

from __future__ import annotations

from typing import Optional

import typer

app = typer.Typer(help="Set or show the focused job for this shell.")


@app.callback(invoke_without_command=True)
def focus(
    job_id: Optional[str] = typer.Argument(None, help="Job ID to focus (W-1)"),
    clear: bool = typer.Option(False, "--clear", help="Clear focus"),
) -> None:
    """Set, show, or clear the focused job for this shell session."""
    if clear:
        typer.echo("Not yet implemented: clear focus")
    elif job_id:
        typer.echo(f"Not yet implemented: focus on {job_id}")
    else:
        typer.echo("Not yet implemented: show current focus")
```

Update `src/devteam/cli/main.py`:
```python
"""Typer CLI entry point for devteam."""

import typer

from devteam.cli.commands import daemon_cmd, focus_cmd, init_cmd, project_cmd
from devteam.cli.commands.job_cmd import register_job_commands

app = typer.Typer(
    name="devteam",
    help="Durable AI Development Team Orchestrator",
    no_args_is_help=True,
)

# Register command groups
app.add_typer(init_cmd.app, name="init")
app.add_typer(daemon_cmd.app, name="daemon")
app.add_typer(project_cmd.app, name="project")
app.add_typer(focus_cmd.app, name="focus")

# Register top-level job control commands
register_job_commands(app)


def main() -> None:
    app()
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pixi run pytest tests/test_cli.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/cli/ tests/test_cli.py
git commit -m "feat: add CLI stubs for start, status, stop, pause, resume, cancel, focus"
```

---

### Task 9: DBOS Initialization with SQLite

**Files:**
- Create: `src/devteam/daemon/database.py`
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

`tests/test_database.py`:
```python
"""Tests for DBOS/SQLite initialization."""

from pathlib import Path

import pytest

from devteam.daemon.database import (
    get_db_path,
    init_database,
    DatabaseConfig,
)


class TestDatabaseConfig:
    def test_default_db_path(self, tmp_devteam_home: Path) -> None:
        db_path = get_db_path(tmp_devteam_home)
        assert db_path == tmp_devteam_home / "devteam.sqlite"

    def test_database_config(self, tmp_devteam_home: Path) -> None:
        config = DatabaseConfig(devteam_home=tmp_devteam_home)
        assert config.db_path == tmp_devteam_home / "devteam.sqlite"
        assert config.db_url.startswith("sqlite:///")


class TestDatabaseInit:
    def test_init_creates_config(self, tmp_devteam_home: Path) -> None:
        """init_database returns a valid DatabaseConfig."""
        config = init_database(tmp_devteam_home)
        assert isinstance(config, DatabaseConfig)
        assert config.devteam_home == tmp_devteam_home

    def test_init_idempotent(self, tmp_devteam_home: Path) -> None:
        """Calling init_database twice does not error."""
        config1 = init_database(tmp_devteam_home)
        config2 = init_database(tmp_devteam_home)
        assert config1.db_path == config2.db_path
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pixi run pytest tests/test_database.py -v
```
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/devteam/daemon/database.py`:
```python
"""DBOS/SQLite database initialization and configuration.

DBOS uses SQLite for durable workflow state persistence. The database
lives at ~/.devteam/devteam.sqlite. This module handles configuration
and initialization — actual DBOS workflow registration happens in the
workflow modules (Plans 2+).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DatabaseConfig:
    """Configuration for the DBOS SQLite database."""

    devteam_home: Path

    @property
    def db_path(self) -> Path:
        return self.devteam_home / "devteam.sqlite"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


def get_db_path(devteam_home: Path) -> Path:
    """Return the path to the SQLite database."""
    return devteam_home / "devteam.sqlite"


def init_database(devteam_home: Path) -> DatabaseConfig:
    """Initialize the database configuration.

    In Plan 2 (Agent Invocation), this will also initialize DBOS with:
      - DBOS.launch() for workflow engine startup
      - Table creation for entity state tracking
      - WAL mode for crash resilience

    For now, returns the configuration that the daemon will use.
    """
    config = DatabaseConfig(devteam_home=devteam_home)

    # Ensure the parent directory exists
    config.db_path.parent.mkdir(parents=True, exist_ok=True)

    return config
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pixi run pytest tests/test_database.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/daemon/database.py tests/test_database.py
git commit -m "feat: add DBOS/SQLite database config and initialization scaffold"
```

---

### Task 10: Integration — Wire Everything Together and Final Verification

**Files:**
- Update: `src/devteam/daemon/server.py` (add database config)
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write the failing test**

`tests/test_integration.py`:
```python
"""Integration tests — verify the full stack wires together."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from devteam.cli.main import app
from devteam.config.settings import DevteamConfig, load_global_config
from devteam.daemon.database import DatabaseConfig, init_database
from devteam.daemon.process import (
    DaemonState,
    acquire_pid_lock,
    get_daemon_state,
    release_pid_lock,
)
from devteam.models.entities import Job, JobStatus, Task, TaskStatus
from devteam.models.state import validate_job_transition, validate_task_transition

runner = CliRunner()


class TestFullInitFlow:
    def test_init_then_daemon_status(self, tmp_path: Path) -> None:
        """init creates the structure, daemon status reports not running."""
        devteam_home = tmp_path / ".devteam"

        with patch("devteam.cli.commands.init_cmd.get_devteam_home", return_value=devteam_home):
            result = runner.invoke(app, ["init"])
        assert result.exit_code == 0

        # Config file is loadable
        config = load_global_config(devteam_home / "config.toml")
        assert config.daemon.port == 7432

        # Database can initialize
        db_config = init_database(devteam_home)
        assert db_config.db_path.parent.exists()

        # Daemon status works
        with patch("devteam.cli.commands.daemon_cmd.get_devteam_home", return_value=devteam_home):
            result = runner.invoke(app, ["daemon", "status"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()


class TestEntityLifecycleFlow:
    def test_job_through_lifecycle(self) -> None:
        """A Job can transition through the full happy path."""
        job = Job(job_id="W-1", title="Integration Test")
        assert job.status == JobStatus.CREATED

        # Simulate lifecycle transitions
        transitions = [
            JobStatus.PLANNING,
            JobStatus.DECOMPOSING,
            JobStatus.EXECUTING,
            JobStatus.REVIEWING,
            JobStatus.COMPLETED,
        ]
        current = job.status
        for next_status in transitions:
            validate_job_transition(current, next_status)
            current = next_status

    def test_task_with_question_flow(self) -> None:
        """A Task can pause for a question and resume."""
        task = Task(
            task_id="T-1",
            job_id="W-1",
            description="Build auth",
            assigned_to="backend",
            app="api",
        )

        # queued -> assigned -> executing -> waiting_on_question -> executing -> waiting_on_review -> approved -> completed
        transitions = [
            TaskStatus.ASSIGNED,
            TaskStatus.EXECUTING,
            TaskStatus.WAITING_ON_QUESTION,
            TaskStatus.EXECUTING,
            TaskStatus.WAITING_ON_REVIEW,
            TaskStatus.APPROVED,
            TaskStatus.COMPLETED,
        ]
        current = task.status
        for next_status in transitions:
            validate_task_transition(current, next_status)
            current = next_status

    def test_task_revision_loop(self) -> None:
        """A Task can go through multiple revision cycles."""
        transitions = [
            (TaskStatus.QUEUED, TaskStatus.ASSIGNED),
            (TaskStatus.ASSIGNED, TaskStatus.EXECUTING),
            (TaskStatus.EXECUTING, TaskStatus.WAITING_ON_REVIEW),
            (TaskStatus.WAITING_ON_REVIEW, TaskStatus.REVISION_REQUESTED),
            (TaskStatus.REVISION_REQUESTED, TaskStatus.EXECUTING),
            (TaskStatus.EXECUTING, TaskStatus.WAITING_ON_REVIEW),
            (TaskStatus.WAITING_ON_REVIEW, TaskStatus.APPROVED),
            (TaskStatus.APPROVED, TaskStatus.COMPLETED),
        ]
        for from_state, to_state in transitions:
            validate_task_transition(from_state, to_state)


class TestPIDLockIntegration:
    def test_acquire_release_cycle(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        port_path = tmp_devteam_home / "daemon.port"

        # Initially not running
        state = get_daemon_state(pid_path, port_path)
        assert state.running is False

        # Acquire lock
        acquire_pid_lock(pid_path, os.getpid())
        state = get_daemon_state(pid_path, port_path)
        assert state.running is True

        # Release lock
        release_pid_lock(pid_path)
        state = get_daemon_state(pid_path, port_path)
        assert state.running is False
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pixi run pytest tests/test_integration.py -v
```
Expected: FAIL initially if any wiring is broken (should pass once all modules are in place)

- [ ] **Step 3: Fix any wiring issues**

All code has been written in previous tasks. If any import errors or integration issues surface, fix them in the relevant module.

- [ ] **Step 4: Run the full test suite**

Run:
```bash
pixi run pytest tests/ -v
```
Expected: ALL tests PASS across all test files

- [ ] **Step 5: Verify the CLI works end-to-end**

Run:
```bash
pixi run devteam --help
pixi run devteam init --help
pixi run devteam daemon --help
pixi run devteam start --help
pixi run devteam status --help
pixi run devteam project --help
pixi run devteam focus --help
```
Expected: All commands display their help text correctly

- [ ] **Step 6: Commit**

```bash
git add tests/test_integration.py
git commit -m "feat: add integration tests verifying full init, lifecycle, and PID lock flows"
```

---

## Summary

| Task | What It Builds | Key Files |
|------|---------------|-----------|
| 1 | Project scaffolding, pixi, package structure | `pixi.toml`, `pyproject.toml`, `src/devteam/` |
| 2 | Entity models with Pydantic validation | `src/devteam/models/entities.py` |
| 3 | State machine transitions | `src/devteam/models/state.py` |
| 4 | TOML config loading with merge | `src/devteam/config/settings.py` |
| 5 | PID file management and singleton lock | `src/devteam/daemon/process.py` |
| 6 | FastAPI server with stub endpoints | `src/devteam/daemon/server.py` |
| 7 | CLI commands: init, daemon, project | `src/devteam/cli/commands/` |
| 8 | CLI commands: start, status, stop, pause, resume, cancel, focus | `src/devteam/cli/commands/job_cmd.py` |
| 9 | DBOS/SQLite database scaffold | `src/devteam/daemon/database.py` |
| 10 | Integration tests, final verification | `tests/test_integration.py` |

**After this plan is complete, the following Plan 2+ features have stub hooks ready:**
- `POST /api/v1/jobs` — stub for job creation (Plan 2: Agent Invocation)
- `POST /api/v1/jobs/{id}/stop|pause|resume|cancel` — stubs for job control (Plan 2)
- `POST /api/v1/jobs/{id}/questions/{id}/answer` — stub for question answering (Plan 2)
- `POST /api/v1/focus` — stub for focus management (Plan 2)
- `POST /api/v1/projects` — stub for project registration (Plan 3: Git Operations)
- `DatabaseConfig` — ready for DBOS `launch()` call (Plan 2)
- Entity models and state machines — ready for workflow persistence (Plan 2)
