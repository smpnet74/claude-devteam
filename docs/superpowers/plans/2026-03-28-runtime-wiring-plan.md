# Runtime Wiring Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire all Phase 2-6 scaffolding into a working single-operator system by placing DBOS durable workflows at the center, removing stopgap systems, and building an interactive terminal experience.

**Architecture:** Single-process CLI hosts an async event loop. DBOS manages workflow durability via SQLite. A separate `~/.devteam/runtime.sqlite` stores durable alias mappings (W-1→UUID), artifact tracking, and question routing. Existing pure business logic (routing, decomposition, review, escalation) is preserved with thin DBOS wrappers. Old stopgap code (daemon, JobStore, queue) is deleted last, not first.

**Tech Stack:** Python 3.13, DBOS SDK v2.16+ (SQLite backend), prompt_toolkit, Claude Agent SDK, Typer CLI, Pydantic v2, SurrealDB (optional knowledge), Ollama (optional embeddings)

**Spec:** `docs/superpowers/specs/2026-03-28-runtime-wiring-design.md`

---

## Design Principles

1. **Build alongside, cut over, then delete.** New DBOS runtime coexists with old code. Old code is deleted only after the new path is fully validated.
2. **Wrap, don't rewrite.** Existing tested pure functions (routing, decomposition, review, escalation) stay unchanged. DBOS decorators go on thin wrappers in a new `runtime.py`.
3. **Runtime state model first.** Durable alias→UUID mapping, artifact tracking, and question routing are the foundation. Without them, resume/status/answer/cleanup all break.
4. **Simple CLI before fancy TUI.** Stdout streaming + stdin prompts deliver human-usable value immediately. prompt_toolkit split-pane comes after the runtime is solid.
5. **Use real API signatures.** All git, contract, and DBOS calls use verified signatures from the actual codebase.

---

## File Structure

### Files to Create

| File | Responsibility |
|------|---------------|
| `src/devteam/orchestrator/runtime_state.py` | Durable SQLite registry: job aliases, task aliases, question routing, artifact tracking |
| `src/devteam/orchestrator/runtime.py` | Thin `@DBOS.step()` / `@DBOS.workflow()` wrappers around existing pure logic |
| `src/devteam/orchestrator/bootstrap.py` | Config → DBOS init → service wiring → workflow start |
| `src/devteam/orchestrator/events.py` | Event types, `emit_log` helper, formatters |
| `src/devteam/cli/interactive.py` | prompt_toolkit session (Phase 8, built on simple CLI from Phase 5) |
| `tests/orchestrator/test_runtime_state.py` | Tests for runtime state CRUD |
| `tests/orchestrator/test_runtime.py` | Tests for DBOS wrappers |
| `tests/orchestrator/test_bootstrap.py` | Tests for bootstrap sequence |
| `tests/orchestrator/test_events.py` | Tests for event types and formatters |
| `tests/cli/test_interactive.py` | Tests for command parsing and dispatch |
| `tests/test_e2e_workflow.py` | End-to-end integration tests |
| `tests/conftest_dbos.py` | Shared DBOS test fixtures |

### Files to Modify (late, during cleanup phase)

| File | Change |
|------|--------|
| `src/devteam/cli/commands/job_cmd.py` | `start` launches DBOS workflow; `resume` recovers; `status`/`answer` use runtime_state |
| `src/devteam/cli/main.py` | Remove daemon commands (Phase 9 only) |
| `src/devteam/concurrency/__init__.py` | Remove re-exports of deleted modules (Phase 9 only) |
| `src/devteam/concurrency/status_display.py` | Remove queue import, adapt for DBOS (Phase 9 only) |
| `src/devteam/concurrency/cli_priority.py` | Remove queue import, adapt for DBOS (Phase 9 only) |
| `pyproject.toml` | Add `prompt_toolkit`, move `fastapi`/`uvicorn` to dev-only |
| `src/devteam/config/settings.py` | Add `InteractiveConfig`, `project_name` to `GeneralConfig` |

### Files to Delete (Phase 9 only — after new runtime is validated)

| File | Reason |
|------|--------|
| `src/devteam/daemon/server.py` | No FastAPI daemon in V1 |
| `src/devteam/daemon/process.py` | No PID file management |
| `src/devteam/daemon/database.py` | DBOS manages its own SQLite |
| `src/devteam/orchestrator/cli_bridge.py` | Replaced by runtime_state + DBOS |
| `src/devteam/orchestrator/jobs.py` | Replaced by DBOS workflows |
| `src/devteam/concurrency/queue.py` | Replaced by DBOS workflow concurrency |
| `src/devteam/concurrency/durable_sleep.py` | Replaced by `DBOS.sleep_async()` |
| `src/devteam/concurrency/invoke.py` | Replaced by in-step retry |
| Corresponding test files | Tests for removed modules |

---

## Task 1: Runtime State Model

The most important missing piece. Define a durable SQLite-backed registry for alias mapping, artifact tracking, and question routing. Without this, resume/status/answer all break after restart.

**Files:**
- Create: `src/devteam/orchestrator/runtime_state.py`
- Create: `tests/orchestrator/test_runtime_state.py`

- [ ] **Step 1: Write failing tests for RuntimeStateStore**

Create `tests/orchestrator/test_runtime_state.py`:

```python
"""Tests for durable runtime state store."""

import time

import pytest

from devteam.orchestrator.runtime_state import (
    ArtifactRecord,
    JobRecord,
    QuestionRecord,
    RuntimeStateStore,
    TaskRecord,
)


@pytest.fixture
def store(tmp_path):
    s = RuntimeStateStore(str(tmp_path / "runtime.sqlite"))
    yield s
    s.close()


class TestJobRegistry:
    def test_register_and_get(self, store):
        job = store.register_job(
            workflow_id="uuid-123",
            project_name="myproject",
            repo_root="/home/user/myproject",
        )
        assert job.alias == "W-1"
        assert job.workflow_id == "uuid-123"

        fetched = store.get_job("W-1")
        assert fetched is not None
        assert fetched.workflow_id == "uuid-123"

    def test_sequential_aliases(self, store):
        j1 = store.register_job(workflow_id="a", project_name="p", repo_root="/r")
        j2 = store.register_job(workflow_id="b", project_name="p", repo_root="/r")
        assert j1.alias == "W-1"
        assert j2.alias == "W-2"

    def test_aliases_survive_reopen(self, tmp_path):
        db_path = str(tmp_path / "survive.sqlite")
        s1 = RuntimeStateStore(db_path)
        s1.register_job(workflow_id="a", project_name="p", repo_root="/r")
        s1.close()

        s2 = RuntimeStateStore(db_path)
        j = s2.register_job(workflow_id="b", project_name="p", repo_root="/r")
        assert j.alias == "W-2"  # continues from W-1, not resets to W-1
        s2.close()

    def test_get_by_workflow_id(self, store):
        store.register_job(workflow_id="uuid-abc", project_name="p", repo_root="/r")
        job = store.get_job_by_workflow_id("uuid-abc")
        assert job is not None
        assert job.alias == "W-1"

    def test_update_status(self, store):
        store.register_job(workflow_id="a", project_name="p", repo_root="/r")
        store.update_job_status("W-1", "completed")
        job = store.get_job("W-1")
        assert job.status == "completed"

    def test_get_active_jobs(self, store):
        store.register_job(workflow_id="a", project_name="p", repo_root="/r")
        store.register_job(workflow_id="b", project_name="p", repo_root="/r")
        store.update_job_status("W-1", "completed")
        active = store.get_active_jobs()
        assert len(active) == 1
        assert active[0].alias == "W-2"


class TestTaskRegistry:
    def test_register_and_get(self, store):
        store.register_job(workflow_id="parent", project_name="p", repo_root="/r")
        task = store.register_task(
            alias="T-1", workflow_id="child-uuid", job_alias="W-1",
            assigned_to="backend_engineer",
        )
        assert task.alias == "T-1"
        fetched = store.get_task("T-1")
        assert fetched is not None
        assert fetched.workflow_id == "child-uuid"

    def test_get_tasks_for_job(self, store):
        store.register_job(workflow_id="parent", project_name="p", repo_root="/r")
        store.register_task(alias="T-1", workflow_id="c1", job_alias="W-1", assigned_to="backend_engineer")
        store.register_task(alias="T-2", workflow_id="c2", job_alias="W-1", assigned_to="frontend_engineer")
        tasks = store.get_tasks_for_job("W-1")
        assert len(tasks) == 2


class TestQuestionRegistry:
    def test_register_and_lookup(self, store):
        display = store.register_question(
            internal_id="Q-T2-1",
            child_workflow_id="child-uuid",
            task_alias="T-2",
            text="Redis or JWT?",
            tier=2,
        )
        assert display == "Q-1"
        q = store.lookup_question("Q-1")
        assert q is not None
        assert q.internal_id == "Q-T2-1"
        assert q.child_workflow_id == "child-uuid"

    def test_resolve(self, store):
        store.register_question(
            internal_id="Q-T2-1", child_workflow_id="c", task_alias="T-2",
            text="Redis or JWT?", tier=2,
        )
        q = store.resolve_question("Q-1")
        assert q is not None
        assert q.resolved is True
        q2 = store.lookup_question("Q-1")
        assert q2.resolved is True

    def test_get_pending(self, store):
        store.register_question(internal_id="a", child_workflow_id="c1", task_alias="T-1", text="Q1", tier=2)
        store.register_question(internal_id="b", child_workflow_id="c2", task_alias="T-2", text="Q2", tier=1)
        store.resolve_question("Q-1")
        pending = store.get_pending_questions()
        assert len(pending) == 1
        assert pending[0].display_alias == "Q-2"


class TestArtifactRegistry:
    def test_register_and_get(self, store):
        store.register_job(workflow_id="p", project_name="p", repo_root="/r")
        store.register_task(alias="T-1", workflow_id="c", job_alias="W-1", assigned_to="be")
        store.register_artifact(task_alias="T-1", worktree_path="/wt/T-1", branch_name="devteam/feat/T-1")
        art = store.get_artifact("T-1")
        assert art is not None
        assert art.worktree_path == "/wt/T-1"
        assert art.branch_name == "devteam/feat/T-1"

    def test_update_pr(self, store):
        store.register_job(workflow_id="p", project_name="p", repo_root="/r")
        store.register_task(alias="T-1", workflow_id="c", job_alias="W-1", assigned_to="be")
        store.register_artifact(task_alias="T-1", worktree_path="/wt", branch_name="b")
        store.update_pr(task_alias="T-1", pr_number=42, pr_url="https://github.com/x/y/pull/42", pr_state="open")
        art = store.get_artifact("T-1")
        assert art.pr_number == 42
        assert art.pr_state == "open"

    def test_get_artifacts_for_job(self, store):
        store.register_job(workflow_id="p", project_name="p", repo_root="/r")
        store.register_task(alias="T-1", workflow_id="c1", job_alias="W-1", assigned_to="be")
        store.register_task(alias="T-2", workflow_id="c2", job_alias="W-1", assigned_to="fe")
        store.register_artifact(task_alias="T-1", worktree_path="/wt1", branch_name="b1")
        store.register_artifact(task_alias="T-2", worktree_path="/wt2", branch_name="b2")
        artifacts = store.get_artifacts_for_job("W-1")
        assert len(artifacts) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/orchestrator/test_runtime_state.py -v`

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement runtime_state.py**

Create `src/devteam/orchestrator/runtime_state.py`:

```python
"""Durable runtime state store — SQLite-backed registry for aliases, artifacts, and questions.

Persists at ~/.devteam/runtime.sqlite. Survives process restart.
Used by bootstrap, resume, status, answer, and cleanup operations.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JobRecord:
    alias: str            # W-1
    workflow_id: str       # DBOS UUID
    project_name: str
    repo_root: str
    status: str           # active, paused, cancelling, completed, failed, cancelled
    created_at: float


@dataclass(frozen=True)
class TaskRecord:
    alias: str            # T-1
    workflow_id: str       # DBOS child UUID
    job_alias: str        # W-1
    assigned_to: str      # backend_engineer
    status: str           # pending, running, completed, failed, cancelled


@dataclass(frozen=True)
class QuestionRecord:
    display_alias: str     # Q-1 (operator-facing)
    internal_id: str       # Q-T2-1 (task-scoped DBOS event key)
    child_workflow_id: str
    task_alias: str        # T-2
    text: str
    tier: int             # 1 = blocking, 2 = non-blocking
    resolved: bool


@dataclass(frozen=True)
class ArtifactRecord:
    task_alias: str        # T-1
    worktree_path: str
    branch_name: str
    pr_number: int | None
    pr_url: str | None
    pr_state: str | None   # open, merged, closed


class RuntimeStateStore:
    """SQLite-backed registry for runtime metadata.

    NOT part of DBOS's system database — this is our own store for
    alias mapping, artifact tracking, and question routing.
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS job_registry (
                alias TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL UNIQUE,
                project_name TEXT NOT NULL DEFAULT '',
                repo_root TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_registry (
                alias TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL UNIQUE,
                job_alias TEXT NOT NULL REFERENCES job_registry(alias),
                assigned_to TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE IF NOT EXISTS question_registry (
                display_alias TEXT PRIMARY KEY,
                internal_id TEXT NOT NULL,
                child_workflow_id TEXT NOT NULL,
                task_alias TEXT NOT NULL,
                text TEXT NOT NULL,
                tier INTEGER NOT NULL DEFAULT 2,
                resolved INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS artifact_registry (
                task_alias TEXT PRIMARY KEY REFERENCES task_registry(alias),
                worktree_path TEXT NOT NULL DEFAULT '',
                branch_name TEXT NOT NULL DEFAULT '',
                pr_number INTEGER,
                pr_url TEXT,
                pr_state TEXT
            );
        """)
        self._conn.commit()

    # -- Job operations --

    def _next_job_alias(self) -> str:
        row = self._conn.execute(
            "SELECT alias FROM job_registry ORDER BY CAST(SUBSTR(alias, 3) AS INTEGER) DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return "W-1"
        num = int(row[0].split("-")[1])
        return f"W-{num + 1}"

    def register_job(
        self, workflow_id: str, project_name: str, repo_root: str,
    ) -> JobRecord:
        alias = self._next_job_alias()
        now = time.time()
        self._conn.execute(
            "INSERT INTO job_registry (alias, workflow_id, project_name, repo_root, status, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (alias, workflow_id, project_name, repo_root, now),
        )
        self._conn.commit()
        return JobRecord(alias=alias, workflow_id=workflow_id, project_name=project_name,
                         repo_root=repo_root, status="active", created_at=now)

    def get_job(self, alias: str) -> JobRecord | None:
        row = self._conn.execute(
            "SELECT alias, workflow_id, project_name, repo_root, status, created_at "
            "FROM job_registry WHERE alias = ?", (alias,)
        ).fetchone()
        if row is None:
            return None
        return JobRecord(*row)

    def get_job_by_workflow_id(self, workflow_id: str) -> JobRecord | None:
        row = self._conn.execute(
            "SELECT alias, workflow_id, project_name, repo_root, status, created_at "
            "FROM job_registry WHERE workflow_id = ?", (workflow_id,)
        ).fetchone()
        if row is None:
            return None
        return JobRecord(*row)

    def update_job_status(self, alias: str, status: str) -> None:
        self._conn.execute("UPDATE job_registry SET status = ? WHERE alias = ?", (status, alias))
        self._conn.commit()

    def get_active_jobs(self) -> list[JobRecord]:
        rows = self._conn.execute(
            "SELECT alias, workflow_id, project_name, repo_root, status, created_at "
            "FROM job_registry WHERE status IN ('active', 'paused')"
        ).fetchall()
        return [JobRecord(*r) for r in rows]

    # -- Task operations --

    def register_task(
        self, alias: str, workflow_id: str, job_alias: str, assigned_to: str,
    ) -> TaskRecord:
        self._conn.execute(
            "INSERT INTO task_registry (alias, workflow_id, job_alias, assigned_to, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (alias, workflow_id, job_alias, assigned_to),
        )
        self._conn.commit()
        return TaskRecord(alias=alias, workflow_id=workflow_id, job_alias=job_alias,
                          assigned_to=assigned_to, status="pending")

    def get_task(self, alias: str) -> TaskRecord | None:
        row = self._conn.execute(
            "SELECT alias, workflow_id, job_alias, assigned_to, status "
            "FROM task_registry WHERE alias = ?", (alias,)
        ).fetchone()
        if row is None:
            return None
        return TaskRecord(*row)

    def get_tasks_for_job(self, job_alias: str) -> list[TaskRecord]:
        rows = self._conn.execute(
            "SELECT alias, workflow_id, job_alias, assigned_to, status "
            "FROM task_registry WHERE job_alias = ?", (job_alias,)
        ).fetchall()
        return [TaskRecord(*r) for r in rows]

    def update_task_status(self, alias: str, status: str) -> None:
        self._conn.execute("UPDATE task_registry SET status = ? WHERE alias = ?", (status, alias))
        self._conn.commit()

    # -- Question operations --

    def _next_question_alias(self) -> str:
        row = self._conn.execute(
            "SELECT display_alias FROM question_registry "
            "ORDER BY CAST(SUBSTR(display_alias, 3) AS INTEGER) DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return "Q-1"
        num = int(row[0].split("-")[1])
        return f"Q-{num + 1}"

    def register_question(
        self, internal_id: str, child_workflow_id: str, task_alias: str,
        text: str, tier: int,
    ) -> str:
        display = self._next_question_alias()
        self._conn.execute(
            "INSERT INTO question_registry "
            "(display_alias, internal_id, child_workflow_id, task_alias, text, tier, resolved) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (display, internal_id, child_workflow_id, task_alias, text, tier),
        )
        self._conn.commit()
        return display

    def lookup_question(self, display_alias: str) -> QuestionRecord | None:
        row = self._conn.execute(
            "SELECT display_alias, internal_id, child_workflow_id, task_alias, text, tier, resolved "
            "FROM question_registry WHERE display_alias = ?", (display_alias,)
        ).fetchone()
        if row is None:
            return None
        return QuestionRecord(*row[:6], resolved=bool(row[6]))

    def resolve_question(self, display_alias: str) -> QuestionRecord | None:
        self._conn.execute(
            "UPDATE question_registry SET resolved = 1 WHERE display_alias = ?", (display_alias,)
        )
        self._conn.commit()
        return self.lookup_question(display_alias)

    def get_pending_questions(self, job_alias: str | None = None) -> list[QuestionRecord]:
        if job_alias:
            rows = self._conn.execute(
                "SELECT q.display_alias, q.internal_id, q.child_workflow_id, q.task_alias, "
                "q.text, q.tier, q.resolved "
                "FROM question_registry q JOIN task_registry t ON q.task_alias = t.alias "
                "WHERE q.resolved = 0 AND t.job_alias = ?", (job_alias,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT display_alias, internal_id, child_workflow_id, task_alias, text, tier, resolved "
                "FROM question_registry WHERE resolved = 0"
            ).fetchall()
        return [QuestionRecord(*r[:6], resolved=bool(r[6])) for r in rows]

    # -- Artifact operations --

    def register_artifact(
        self, task_alias: str, worktree_path: str, branch_name: str,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO artifact_registry (task_alias, worktree_path, branch_name) "
            "VALUES (?, ?, ?)",
            (task_alias, worktree_path, branch_name),
        )
        self._conn.commit()

    def update_pr(
        self, task_alias: str, pr_number: int, pr_url: str, pr_state: str,
    ) -> None:
        self._conn.execute(
            "UPDATE artifact_registry SET pr_number = ?, pr_url = ?, pr_state = ? "
            "WHERE task_alias = ?",
            (pr_number, pr_url, pr_state, task_alias),
        )
        self._conn.commit()

    def get_artifact(self, task_alias: str) -> ArtifactRecord | None:
        row = self._conn.execute(
            "SELECT task_alias, worktree_path, branch_name, pr_number, pr_url, pr_state "
            "FROM artifact_registry WHERE task_alias = ?", (task_alias,)
        ).fetchone()
        if row is None:
            return None
        return ArtifactRecord(*row)

    def get_artifacts_for_job(self, job_alias: str) -> list[ArtifactRecord]:
        rows = self._conn.execute(
            "SELECT a.task_alias, a.worktree_path, a.branch_name, a.pr_number, a.pr_url, a.pr_state "
            "FROM artifact_registry a JOIN task_registry t ON a.task_alias = t.alias "
            "WHERE t.job_alias = ?", (job_alias,)
        ).fetchall()
        return [ArtifactRecord(*r) for r in rows]

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/orchestrator/test_runtime_state.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/orchestrator/runtime_state.py tests/orchestrator/test_runtime_state.py
git commit -m "feat: add durable runtime state store for alias mapping, artifacts, and questions"
```

---

## Task 2: DBOS Test Fixture and Dependencies

Add DBOS test fixtures, prompt_toolkit dependency, and config updates.

**Files:**
- Create: `tests/conftest_dbos.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_dbos_smoke.py`
- Modify: `pyproject.toml`
- Modify: `src/devteam/config/settings.py`

- [ ] **Step 1: Write DBOS test fixture**

Create `tests/conftest_dbos.py`:

```python
"""Shared DBOS test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def dbos_db_path(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'dbos_test.sqlite'}"


@pytest.fixture
def dbos_launch(dbos_db_path: str):
    from dbos import DBOS

    DBOS(config={"name": "devteam_test", "system_database_url": dbos_db_path})
    DBOS.launch()
    yield DBOS
    DBOS.destroy()
```

- [ ] **Step 2: Import in conftest.py and write smoke test**

Add to `tests/conftest.py`:

```python
from tests.conftest_dbos import dbos_db_path, dbos_launch  # noqa: F401
```

Create `tests/test_dbos_smoke.py`:

```python
"""Smoke test for DBOS test fixture."""

import pytest
from dbos import DBOS


@pytest.mark.asyncio
async def test_dbos_workflow_runs(dbos_launch):
    @DBOS.workflow()
    async def hello(name: str) -> str:
        return f"hello {name}"

    assert await hello("world") == "hello world"


@pytest.mark.asyncio
async def test_dbos_step_runs(dbos_launch):
    @DBOS.step()
    async def add(a: int, b: int) -> int:
        return a + b

    @DBOS.workflow()
    async def add_wf(a: int, b: int) -> int:
        return await add(a, b)

    assert await add_wf(3, 4) == 7
```

- [ ] **Step 3: Add InteractiveConfig and project_name to settings.py**

In `src/devteam/config/settings.py`, add after `GitConfig`:

```python
class InteractiveConfig(BaseModel):
    """Interactive terminal UI settings."""

    poll_interval_ms: int = Field(default=200, gt=0)
    max_log_lines: int = Field(default=1000, gt=0)
```

Add `project_name` to `GeneralConfig`:

```python
class GeneralConfig(BaseModel):
    """General operational settings."""

    max_concurrent_agents: int = Field(default=3, gt=0)
    project_name: str = ""
```

Add to `DevteamConfig`:

```python
    interactive: InteractiveConfig = Field(default_factory=InteractiveConfig)
```

- [ ] **Step 4: Update pyproject.toml**

Add `prompt_toolkit` to runtime dependencies. Move `fastapi`/`uvicorn` to a `daemon` extras group:

```toml
dependencies = [
    "dbos>=2.16,<3",
    "typer>=0.24,<1",
    "httpx>=0.28,<1",
    "pydantic>=2.12,<3",
    "pyyaml>=6,<7",
    "claude-agent-sdk==0.1.50",
    "surrealdb>=1.0.8,<2",
    "prompt_toolkit>=3.0,<4",
]

[dependency-groups]
test = ["pytest>=8,<9", "pytest-asyncio>=0.23,<1"]
dev = ["ruff>=0.15,<1", "pyright>=1.1,<2"]
daemon = ["fastapi>=0.135,<1", "uvicorn[standard]>=0.42,<1"]
```

- [ ] **Step 5: Install and run smoke test**

```bash
pixi install
pixi run pytest tests/test_dbos_smoke.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/conftest_dbos.py tests/conftest.py tests/test_dbos_smoke.py pyproject.toml pixi.lock src/devteam/config/settings.py
git commit -m "chore: add DBOS test fixtures, prompt_toolkit dep, InteractiveConfig"
```

---

## Task 3: Event Types and Formatters

**Files:**
- Create: `src/devteam/orchestrator/events.py`
- Create: `tests/orchestrator/test_events.py`

- [ ] **Step 1: Write failing tests**

Create `tests/orchestrator/test_events.py`:

```python
"""Tests for workflow event types and formatters."""

from devteam.orchestrator.events import EventLevel, LogEvent, format_log_event, make_log_key


class TestLogEvent:
    def test_create(self):
        evt = LogEvent(message="Task started", level=EventLevel.INFO, seq=1)
        assert evt.message == "Task started"
        assert evt.seq == 1

    def test_format_job(self):
        evt = LogEvent(message="Routing... full_project", level=EventLevel.INFO, seq=1)
        assert "[W-1]" in format_log_event(evt, job_id="W-1")

    def test_format_task(self):
        evt = LogEvent(message="starting", level=EventLevel.INFO, seq=1)
        assert "[W-1/T-1]" in format_log_event(evt, job_id="W-1", task_id="T-1")

    def test_format_question(self):
        evt = LogEvent(message="Redis or JWT?", level=EventLevel.QUESTION, seq=1)
        assert "QUESTION" in format_log_event(evt, job_id="W-1", task_id="T-2")


class TestMakeLogKey:
    def test_padding(self):
        assert make_log_key(1) == "log:000001"
        assert make_log_key(999999) == "log:999999"
```

- [ ] **Step 2: Implement events.py**

Create `src/devteam/orchestrator/events.py`:

```python
"""Workflow event types and formatters for terminal rendering."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class EventLevel(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    QUESTION = "question"
    SUCCESS = "success"


@dataclass(frozen=True)
class LogEvent:
    message: str
    level: EventLevel
    seq: int
    timestamp: float = field(default_factory=time.time)


def make_log_key(seq: int) -> str:
    return f"log:{seq:06d}"


def format_log_event(event: LogEvent, job_id: str, task_id: str | None = None) -> str:
    prefix = f"[{job_id}/{task_id}]" if task_id else f"[{job_id}]"
    if event.level == EventLevel.QUESTION:
        return f"{prefix} QUESTION {event.message}"
    if event.level == EventLevel.ERROR:
        return f"{prefix} ERROR {event.message}"
    return f"{prefix} {event.message}"
```

- [ ] **Step 3: Run tests, commit**

```bash
pixi run pytest tests/orchestrator/test_events.py -v
git add src/devteam/orchestrator/events.py tests/orchestrator/test_events.py
git commit -m "feat: add workflow event types and terminal formatters"
```

---

## Task 4: DBOS Wrappers for Orchestrator Logic

Create thin `@DBOS.step()` wrappers around existing pure functions. The wrappers call `invoke_agent_step` for agent calls and the existing pure functions for everything else. **No changes to routing.py, decomposition.py, review.py, or escalation.py.**

**Files:**
- Create: `src/devteam/orchestrator/runtime.py`
- Create: `tests/orchestrator/test_runtime.py`

- [ ] **Step 1: Write failing tests for route_intake_step**

Create `tests/orchestrator/test_runtime.py`:

```python
"""Tests for DBOS wrappers around pure orchestrator logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dbos import DBOS

from devteam.orchestrator.runtime import route_intake_step, decompose_step
from devteam.orchestrator.schemas import RoutePath, RoutingResult


class TestRouteIntakeStep:
    @pytest.mark.asyncio
    async def test_fast_path(self, dbos_launch):
        """Spec+plan → full_project without agent call."""
        @DBOS.workflow()
        async def wf():
            return await route_intake_step(spec="spec content", plan="plan content")

        result = await wf()
        assert result.path == RoutePath.FULL_PROJECT

    @pytest.mark.asyncio
    async def test_ceo_routing(self, dbos_launch):
        """No spec+plan → invokes CEO agent."""
        ceo_response = MagicMock()
        ceo_response.model_dump.return_value = {"path": "research", "reasoning": "Research request"}

        with patch("devteam.orchestrator.runtime.invoke_agent_step", return_value=ceo_response):

            @DBOS.workflow()
            async def wf():
                return await route_intake_step(spec=None, plan=None, prompt="Research X")

            result = await wf()
            assert result.path == RoutePath.RESEARCH
```

- [ ] **Step 2: Implement runtime.py**

Create `src/devteam/orchestrator/runtime.py`:

```python
"""DBOS step/workflow wrappers around existing pure orchestrator logic.

These wrappers are the boundary between DBOS and our pure business logic.
They add:
- @DBOS.step() / @DBOS.workflow() decorators
- Bridge from async DBOS context to sync InvokerProtocol
- invoke_agent_step calls for agent interactions
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from dbos import DBOS
from pydantic import BaseModel

from devteam.agents.invoker import (
    AgentInvoker,
    InvocationContext,
    InvocationError,
    QueryOptions,
    _run_query,
)
from devteam.agents.registry import AgentRegistry
from devteam.concurrency.rate_limit import _parse_reset_seconds
from devteam.orchestrator.events import make_log_key
from devteam.orchestrator.routing import IntakeContext, classify_intake, build_routing_prompt
from devteam.orchestrator.schemas import (
    DecompositionResult,
    RoutePath,
    RoutingResult,
    TaskDecomposition,
    WorkType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons (set by bootstrap)
# ---------------------------------------------------------------------------

_invoker: AgentInvoker | None = None
_knowledge_store: Any | None = None
_embedder: Any | None = None
_config: dict[str, Any] | None = None


def set_invoker(invoker: AgentInvoker) -> None:
    global _invoker
    _invoker = invoker


def set_knowledge_store(store: Any | None) -> None:
    global _knowledge_store
    _knowledge_store = store


def set_embedder(embedder: Any | None) -> None:
    global _embedder
    _embedder = embedder


def set_config(config: dict[str, Any]) -> None:
    global _config
    _config = config


def get_config() -> dict[str, Any]:
    assert _config is not None, "Config not initialized (call bootstrap first)"
    return _config


# ---------------------------------------------------------------------------
# invoke_agent_step — the core DBOS step for all agent calls
# ---------------------------------------------------------------------------

def _is_rate_limit_error(error: Exception) -> bool:
    msg = str(error).lower()
    return "rate limit" in msg or "retry after" in msg or "429" in msg


@DBOS.step(retries_allowed=False)
async def invoke_agent_step(
    role: str,
    prompt: str,
    worktree_path: str | None,
    project_name: str,
    max_retries: int = 3,
) -> BaseModel:
    """Invoke an agent via Claude SDK with knowledge injection and rate-limit retry.

    Uses the existing AgentInvoker for param building and result parsing.
    Adds: knowledge context in system prompt, query_knowledge in tools,
    and exponential backoff on rate limit errors.
    """
    assert _invoker is not None, "Invoker not initialized (call bootstrap first)"

    context = InvocationContext(
        worktree_path=Path(worktree_path) if worktree_path else Path.cwd(),
        project_name=project_name,
    )

    # Build params using existing invoker logic (schema mapping, model selection, etc.)
    params = _invoker.build_query_params(role, prompt, context)
    options: QueryOptions = params["options"]

    # Knowledge injection: append memory index to system prompt.
    # NOTE: This SurrealDB call happens inside a @DBOS.step(). On crash replay,
    # DBOS returns the cached step result — the knowledge call does NOT re-execute.
    # So non-determinism between original and replay is not a concern.
    if _knowledge_store is not None:
        try:
            from devteam.knowledge.index import build_memory_index_safe

            knowledge_index = await build_memory_index_safe(_knowledge_store, project_name)
            if knowledge_index:
                options = QueryOptions(
                    model=options.model,
                    system_prompt=f"{options.system_prompt}\n\n{knowledge_index}",
                    allowed_tools=[*options.allowed_tools, "query_knowledge"],
                    permission_mode=options.permission_mode,
                    cwd=options.cwd,
                    output_format=options.output_format,
                )
        except Exception:
            pass  # Knowledge failure must not block invocation

    # Rate-limit retry loop
    # NOTE: _get_schema_for_role is private on AgentInvoker. During implementation,
    # rename it to get_schema_class_for_role (public) to avoid fragile coupling.
    # The public schema_for_role() returns a JSON dict, not the model class we need here.
    result_type = _invoker._get_schema_for_role(role)
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            sdk_result = await _run_query(
                prompt=params["prompt"],
                options=options,
                timeout=context.timeout,
            )
            if hasattr(sdk_result, "is_error") and sdk_result.is_error:
                raise InvocationError(
                    f"Agent '{role}' error: {getattr(sdk_result, 'result', 'unknown')}"
                )
            if hasattr(sdk_result, "structured_output") and sdk_result.structured_output is not None:
                raw_data = sdk_result.structured_output
            else:
                raw_data = json.loads(sdk_result.result)
            return result_type.model_validate(raw_data)
        except InvocationError as e:
            if _is_rate_limit_error(e):
                last_error = e
                backoff = _parse_reset_seconds(str(e)) or (60 * (2 ** attempt))
                logger.warning("Rate limit on '%s' (attempt %d), sleeping %ds", role, attempt + 1, backoff)
                await DBOS.sleep_async(float(backoff))
                continue
            raise

    assert last_error is not None
    raise last_error


# ---------------------------------------------------------------------------
# Orchestrator step wrappers
# ---------------------------------------------------------------------------

@DBOS.step()
async def route_intake_step(
    spec: str | None = None,
    plan: str | None = None,
    prompt: str | None = None,
    issue_url: str | None = None,
) -> RoutingResult:
    """DBOS step: route intake using existing pure logic + invoke_agent_step."""
    ctx = IntakeContext(spec=spec, plan=plan, issue_url=issue_url, prompt=prompt)

    # Use existing pure function for fast path
    fast_path = classify_intake(ctx)
    if fast_path == RoutePath.FULL_PROJECT:
        return RoutingResult(
            path=RoutePath.FULL_PROJECT,
            reasoning="Spec and plan provided — direct to full project workflow",
        )

    # CEO analysis: build prompt (pure), then invoke agent (DBOS step)
    routing_prompt = build_routing_prompt(ctx)
    result = await invoke_agent_step(
        role="ceo", prompt=routing_prompt, worktree_path=None, project_name="",
    )
    return RoutingResult.model_validate(result.model_dump())


@DBOS.step()
async def decompose_step(
    spec: str, plan: str, routing: RoutingResult,
) -> DecompositionResult:
    """DBOS step: decompose via CA using existing pure logic."""
    from devteam.orchestrator.decomposition import (
        assign_peer_reviewers,
        build_decomposition_prompt,
        validate_decomposition,
    )

    decomp_prompt = build_decomposition_prompt(spec, plan, routing)
    result = await invoke_agent_step(
        role="chief_architect", prompt=decomp_prompt, worktree_path=None, project_name="",
    )
    decomp = DecompositionResult.model_validate(result.model_dump())

    # Post-process with existing pure logic
    decomp = decomp.model_copy(
        update={"peer_assignments": assign_peer_reviewers(decomp.tasks, decomp.peer_assignments)}
    )
    errors = validate_decomposition(decomp)
    if errors:
        raise ValueError(f"Decomposition validation failed: {'; '.join(errors)}")
    return decomp


@DBOS.step()
async def post_pr_review_step(
    work_type: WorkType, pr_context: str, project_name: str,
    files_changed: list[str] | None = None, assigned_to: str | None = None,
) -> dict[str, Any]:
    """DBOS step: run post-PR review chain."""
    from devteam.orchestrator.review import (
        get_review_chain,
        is_small_fix_with_no_behavior_change,
        sanitize_pr_context,
        ReviewResult as ReviewResultModel,
    )
    from pydantic import ValidationError

    pr_context = sanitize_pr_context(pr_context)
    chain = get_review_chain(work_type, assigned_to=assigned_to)
    gate_results: dict[str, dict] = {}
    failed_gates: list[str] = []
    skipped_gates: list[str] = []

    for gate in chain.gates:
        if (gate.name == "qa_review" and files_changed
                and is_small_fix_with_no_behavior_change(work_type, files_changed)):
            skipped_gates.append(gate.name)
            continue

        raw_result = await invoke_agent_step(
            role=gate.reviewer_role,
            prompt=f"## {gate.name.replace('_', ' ').title()}\n\n{pr_context}\n\nReview and provide your verdict.\n",
            worktree_path=None,
            project_name=project_name,
        )
        # invoke_agent_step returns BaseModel — validate as ReviewResult to access
        # the needs_revision property (which checks verdict in ["needs_revision", "blocked"])
        from devteam.orchestrator.schemas import ReviewResult
        review = ReviewResult.model_validate(raw_result.model_dump())
        gate_results[gate.name] = review.model_dump()

        if review.needs_revision:
            failed_gates.append(gate.name)
            if gate.required:
                break

    required_names = {g.name for g in chain.gates if g.required}
    return {
        "all_passed": not any(g in required_names for g in failed_gates),
        "gate_results": gate_results,
        "failed_gates": failed_gates,
        "skipped_gates": skipped_gates,
    }


# ---------------------------------------------------------------------------
# Git step wrappers (use real git library signatures)
# ---------------------------------------------------------------------------

@DBOS.step()
async def create_worktree_step(
    repo_root: str, task: TaskDecomposition,
) -> str:
    """Create an isolated git worktree for a task. Returns worktree path.

    Uses git/worktree.py:create_worktree(repo_root, branch, worktree_dir, base_ref).
    Idempotent — returns existing worktree if branch already has one.
    """
    from devteam.git.branch import branch_exists, create_feature_branch
    from devteam.git.worktree import create_worktree

    root = Path(repo_root)
    branch_name = f"devteam/{task.pr_group}/{task.id}".replace(" ", "-")

    if not branch_exists(root, branch_name):
        create_feature_branch(root, branch_name)

    wt_info = create_worktree(root, branch_name)
    return str(wt_info.path)


@DBOS.step()
async def create_pr_step(
    repo_root: str, task: TaskDecomposition, worktree_path: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a PR for a task. Idempotent — returns existing PR if found.

    Checks approval gates before push and PR creation.
    Uses git/pr.py:find_existing_pr(cwd, branch) and create_pr(cwd, title, body, branch).
    Approval uses concurrency/approval.py:check_approval(gates, action) -> ApprovalDecision.
    """
    from devteam.concurrency.approval import check_approval, load_approval_gates
    from devteam.git.helpers import git_run
    from devteam.git.pr import create_pr, find_existing_pr

    root = Path(repo_root)
    wt = Path(worktree_path)
    branch_name = f"devteam/{task.pr_group}/{task.id}".replace(" ", "-")

    existing = find_existing_pr(wt, branch_name)
    if existing:
        return {"number": existing.number, "url": existing.url, "state": existing.state}

    # Approval gate: check_approval takes ApprovalGates (not raw dict), action str
    # load_approval_gates builds ApprovalGates from config
    if config:
        gates = load_approval_gates(config.get("approval", {}))
        push_decision = check_approval(gates, "push")
        if push_decision.blocked:
            return {"skipped": True, "reason": "push approval blocked"}
        # Manual approval would emit a Tier 1 question — deferred to interactive wiring

    # Push
    git_run(["push", "-u", "origin", branch_name], cwd=wt)

    if config:
        pr_decision = check_approval(gates, "open_pr")
        if pr_decision.blocked:
            return {"pushed": True, "pr_skipped": True, "reason": "open_pr approval blocked"}

    # Create PR
    pr_info = create_pr(
        cwd=wt,
        title=f"[{task.id}] {task.description[:60]}",
        body=f"Automated PR for task {task.id}\nAssigned to: {task.assigned_to}",
        branch=branch_name,
    )
    return {"number": pr_info.number, "url": pr_info.url, "state": "open"}


@DBOS.step()
async def cleanup_step(repo_root: str, job_alias: str) -> None:
    """Clean up worktrees, branches, and PRs for a completed/cancelled job.

    Uses git/cleanup.py:cleanup_on_cancel(repo_root, pr_branches).
    Reads artifact registry via module singleton (NOT a parameter —
    RuntimeStateStore is not DBOS-serializable).
    """
    from devteam.git.cleanup import cleanup_on_cancel
    from devteam.orchestrator.bootstrap import get_runtime_store

    root = Path(repo_root)
    store = get_runtime_store()
    artifacts = store.get_artifacts_for_job(job_alias)

    pr_branches = []
    for art in artifacts:
        entry: dict[str, Any] = {"branch": art.branch_name}
        if art.pr_number:
            entry["pr_number"] = art.pr_number
        if art.worktree_path:
            entry["worktree_path"] = Path(art.worktree_path)
        pr_branches.append(entry)

    if pr_branches:
        cleanup_on_cancel(root, pr_branches)
```

- [ ] **Step 3: Run tests, commit**

```bash
pixi run pytest tests/orchestrator/test_runtime.py -v
git add src/devteam/orchestrator/runtime.py tests/orchestrator/test_runtime.py
git commit -m "feat: add DBOS step wrappers for routing, decomposition, review, git, and agent invocation"
```

---

## Task 5: execute_task Child Workflow

Build the child workflow that handles engineer → peer review → EM review → PR, with questions and pause support. Uses wrappers from Task 4, NOT rewrites of existing code.

**Files:**
- Create: `src/devteam/orchestrator/workflows.py`
- Create: `tests/orchestrator/test_workflows.py`

- [ ] **Step 1: Write failing tests**

Create `tests/orchestrator/test_workflows.py` with tests for execute_task covering: happy path (approved), question flow, revision loop, max revisions exceeded. Use `@pytest.mark.asyncio` and mock `invoke_agent_step`.

Key assertions and **correct contract values** (from `agents/contracts.py`):
- `ImplementationResult`: `status` must be `Literal["completed", "needs_clarification", "blocked"]`. When `status="needs_clarification"` or `"blocked"`, the `question` field is **required** (not None).
- `ReviewResult`: `verdict` must be `Literal["approved", "approved_with_comments", "needs_revision", "blocked"]` — NOT `"approve"`. The `needs_revision` property returns `True` when `verdict in ("needs_revision", "blocked")`.
- `TaskDecomposition`: uses `depends_on` (not `dependencies`), requires `team: Literal["a","b"]` and `pr_group: str` (min_length=1).
- `RoutingResult`: has `path`, `reasoning`, `target_team` — NO `recommended_role` field.
- Runtime state store gets updated (task status, artifacts)

- [ ] **Step 2: Implement execute_task in workflows.py**

Create `src/devteam/orchestrator/workflows.py`:

The `execute_task` signature must include `repo_root: str` (passed by the parent workflow, which gets it from bootstrap/runtime_state). This is needed for `create_worktree_step(repo_root, task)` and `create_pr_step(repo_root, task, worktree_path)`.

The workflow:
1. Gets its own log counter (local, not global)
2. Calls `create_worktree_step(repo_root, task)` from runtime.py
3. Registers artifact in runtime_state via `get_runtime_store()`
4. Runs revision loop calling `invoke_agent_step`
5. On question: sets DBOS event via `DBOS.set_event()`, waits for answer via `DBOS.recv(topic="answer:Q-T{id}-{n}")` (inside workflow context, use sync API names — DBOS handles async internally)
6. Registers question in runtime_state
7. On peer/EM review pass: calls `create_pr_step`
8. Updates runtime_state task status throughout

Pause/cancel checks use `DBOS.recv(topic="control:pause", timeout_seconds=0)` inside workflow context. **DBOS API convention:** Inside `@DBOS.workflow()` functions, use sync names (`recv`, `set_event`, `send`). From outside workflows (CLI/UI), use `_async` variants (`send_async`, `get_all_events_async`).

- [ ] **Step 3: Run tests, commit**

```bash
pixi run pytest tests/orchestrator/test_workflows.py::TestExecuteTask -v
git commit -m "feat: add execute_task DBOS child workflow with revision loop and question handling"
```

---

## Task 6: execute_job Parent Workflow

Build the parent workflow with DAG-aware parallel execution. Uses existing `DAGState`/`build_dag` from dag.py plus wrappers from runtime.py.

**Files:**
- Modify: `src/devteam/orchestrator/workflows.py` (add execute_job)
- Modify: `tests/orchestrator/test_workflows.py` (add execute_job tests)

- [ ] **Step 1: Write failing tests for execute_job**

Test cases: full_project path (route→decompose→DAG→complete), research path (single agent call), small_fix path (single task). Mock `route_intake_step`, `decompose_step`, and `execute_task`.

**Contract notes for small_fix inline TaskDecomposition:**
- `RoutingResult` has `target_team` (Literal["a","b"] | None), NOT `recommended_role`
- Use `depends_on=[]` not `dependencies=[]`
- `team` and `pr_group` are required fields
- Derive engineer role from team: team "a" → "backend_engineer", team "b" → "data_engineer"

```python
# Correct small_fix construction:
task = TaskDecomposition(
    id="T-1",
    assigned_to="backend_engineer",  # derive from target_team, not recommended_role
    description=spec,
    depends_on=[],          # NOT dependencies
    team=routing.target_team or "a",  # required Literal["a","b"]
    pr_group="fix/small-fix",         # required, min_length=1
)
```

- [ ] **Step 2: Implement execute_job**

The workflow:
1. Registers job in runtime_state
2. Calls `route_intake_step`
3. For research: single `invoke_agent_step`, return
4. For small_fix: create single-task decomposition (derive role from `routing.target_team`, NOT `recommended_role` which doesn't exist; use `depends_on` not `dependencies`; include required `team` and `pr_group` fields), launch one child
5. For full_project: `decompose_step`, then `manage_dag_execution`
6. `manage_dag_execution` uses `DAGState.get_ready_tasks()`, respects `max_concurrent_agents`, launches children via `DBOS.start_workflow_async(execute_task, ...)`
7. Registers each child's task alias + workflow_id in runtime_state
8. Post-PR review via `post_pr_review_step`
9. Cleanup via `cleanup_step`
10. Updates runtime_state job status

- [ ] **Step 3: Run tests, commit**

```bash
pixi run pytest tests/orchestrator/test_workflows.py -v
git commit -m "feat: add execute_job parent workflow with DAG execution"
```

---

## Task 7: Bootstrap Sequence

Wire initialization: config → DBOS → knowledge → registry → runtime_state → workflow start.

**Files:**
- Create: `src/devteam/orchestrator/bootstrap.py`
- Create: `tests/orchestrator/test_bootstrap.py`

- [ ] **Step 1: Write failing tests**

Test: bootstrap returns `(handle, job_id)`. Test: job alias is durable (register in runtime_state). Test: knowledge degradation (SurrealDB unavailable → proceeds).

- [ ] **Step 2: Implement bootstrap.py**

```python
"""Bootstrap: config → DBOS → services → runtime_state → workflow start."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dbos import DBOS

from devteam.agents.invoker import AgentInvoker
from devteam.agents.registry import AgentRegistry
from devteam.agents.template_manager import get_bundled_templates_dir
from devteam.config.settings import DevteamConfig, load_global_config, load_project_config, merge_configs
from devteam.orchestrator.runtime import set_config, set_embedder, set_invoker, set_knowledge_store
from devteam.orchestrator.runtime_state import RuntimeStateStore

logger = logging.getLogger(__name__)

# Module-level singleton for runtime state
_runtime_store: RuntimeStateStore | None = None


def get_runtime_store() -> RuntimeStateStore:
    assert _runtime_store is not None, "Runtime store not initialized"
    return _runtime_store


def load_and_merge_config() -> DevteamConfig:
    global_path = Path.home() / ".devteam" / "config.toml"
    global_config = load_global_config(global_path)
    project_config = load_project_config(Path("devteam.toml"))
    return merge_configs(global_config, project_config)


async def bootstrap(
    spec: str,
    plan: str,
    dbos_db_path: str | None = None,
    runtime_db_path: str | None = None,
) -> tuple[Any, str]:
    """Initialize all services and start the job workflow.

    Returns (WorkflowHandleAsync, job_alias).
    """
    global _runtime_store

    config = load_and_merge_config()

    # DBOS init
    devteam_dir = Path.home() / ".devteam"
    devteam_dir.mkdir(parents=True, exist_ok=True)

    if dbos_db_path is None:
        dbos_db_path = str(devteam_dir / "devteam_system.sqlite")
    DBOS(config={"name": "devteam", "system_database_url": f"sqlite:///{dbos_db_path}"})
    DBOS.launch()

    # Runtime state (our own SQLite, not DBOS's)
    if runtime_db_path is None:
        runtime_db_path = str(devteam_dir / "runtime.sqlite")
    _runtime_store = RuntimeStateStore(runtime_db_path)

    # V1 single-job enforcement
    active = _runtime_store.get_active_jobs()
    if active:
        raise RuntimeError(
            f"Job {active[0].alias} is active. Use 'devteam resume {active[0].alias}' "
            "or 'devteam cancel {active[0].alias}' first."
        )

    # Knowledge (graceful degradation)
    knowledge_store = None
    try:
        from devteam.knowledge.store import KnowledgeStore
        knowledge_store = KnowledgeStore(config.knowledge.surrealdb_url)
        await knowledge_store.connect(
            username=config.knowledge.surrealdb_username,
            password=config.knowledge.surrealdb_password,
        )
    except Exception:
        logger.warning("Knowledge store unavailable — proceeding without knowledge")

    # Embedder (graceful degradation)
    embedder = None
    try:
        from devteam.knowledge.embeddings import create_embedder_from_config
        embedder = create_embedder_from_config(config.knowledge)
        if not await embedder.is_available():
            embedder = None
    except Exception:
        logger.warning("Ollama unavailable — proceeding without embeddings")

    # Agent registry + invoker
    registry = AgentRegistry.load(get_bundled_templates_dir())
    invoker = AgentInvoker(registry)

    # Wire singletons
    set_invoker(invoker)
    set_knowledge_store(knowledge_store)
    set_embedder(embedder)
    set_config(config.model_dump())

    # Start workflow
    from devteam.orchestrator.workflows import execute_job

    repo_root = str(Path.cwd())
    project_name = config.general.project_name or Path.cwd().name

    handle = await DBOS.start_workflow_async(
        execute_job,
        spec=spec,
        plan=plan,
        project_name=project_name,
        repo_root=repo_root,
        config=config.model_dump(),
    )

    # Register in runtime state (durable alias)
    job_record = _runtime_store.register_job(
        workflow_id=handle.workflow_id,
        project_name=project_name,
        repo_root=repo_root,
    )

    return handle, job_record.alias
```

- [ ] **Step 3: Run tests, commit**

```bash
pixi run pytest tests/orchestrator/test_bootstrap.py -v
git commit -m "feat: add bootstrap sequence with durable job registration"
```

---

## Task 8: Simple CLI (stdout + stdin)

Wire `devteam start`, `status`, `answer`, `resume` as simple CLI commands that stream to stdout and prompt on stdin. No prompt_toolkit yet.

**Files:**
- Modify: `src/devteam/cli/commands/job_cmd.py`

- [ ] **Step 1: Implement start command**

`start` calls `bootstrap()`, then enters a simple polling loop:
- Print events from DBOS to stdout
- On Tier 1 question: print question, use `input()` to get answer, send via `DBOS.send_async()`
- On workflow completion: print summary and exit

- [ ] **Step 2: Implement status command**

`status` reads from `RuntimeStateStore`:
- List active jobs with alias, status, task count
- For specific job: list tasks with status

- [ ] **Step 3: Implement answer command**

`answer Q-1 "Use JWT"` reads from question registry to find internal_id and child_workflow_id, then calls `DBOS.send_async()`.

- [ ] **Step 4: Implement resume command**

`resume W-1` reads job record from runtime_state, calls `DBOS.launch()` to recover workflows, then enters the same polling loop as `start`.

- [ ] **Step 5: Run CLI tests, commit**

```bash
pixi run pytest tests/test_cli.py -v
git commit -m "feat: wire CLI start/status/answer/resume with simple stdout streaming"
```

---

## Task 9: Git Integration with Real Library

Wire git operations into workflow steps using actual `devteam.git` function signatures. Track artifacts in runtime_state.

**Files:**
- Already implemented in `runtime.py` (Task 4) with correct signatures
- Create: `tests/orchestrator/test_git_steps.py`

- [ ] **Step 1: Write tests for git steps**

Test `create_worktree_step` and `create_pr_step` with mocked git functions using correct signatures:
- `create_worktree(repo_root: Path, branch: str) -> WorktreeInfo`
- `create_feature_branch(repo_root: Path, branch: str) -> None`
- `find_existing_pr(cwd: Path, branch: str) -> PRInfo | None`
- `create_pr(cwd: Path, title: str, body: str, branch: str) -> PRInfo`
- `cleanup_on_cancel(repo_root: Path, pr_branches: list[dict]) -> CleanupResult`

- [ ] **Step 2: Run tests, commit**

```bash
pixi run pytest tests/orchestrator/test_git_steps.py -v
git commit -m "test: add git step tests with correct library signatures"
```

---

## Task 10: Crash Recovery and Cleanup Tests

Prove DBOS workflow persistence, restart recovery, and artifact cleanup work.

**Files:**
- Create: `tests/test_e2e_workflow.py`

- [ ] **Step 1: Write crash recovery test**

Test: Start workflow → get result → destroy DBOS → re-init → retrieve stored result from same SQLite database. Verify the DBOS recovery mechanism preserves workflow results.

- [ ] **Step 2: Write resume test**

Test: Start a job → register in runtime_state → close store → reopen → verify job record survives with correct alias and workflow_id.

- [ ] **Step 3: Write cleanup test**

Test: Register artifacts (worktree, branch, PR) in runtime_state → call `cleanup_step` with mocked git functions → verify `cleanup_on_cancel` called with correct arguments from artifact registry.

- [ ] **Step 4: Write question flow test**

Test: Start `execute_task` → workflow raises question (sets DBOS event) → register in runtime_state → send answer via `DBOS.send_async()` → workflow resumes and completes.

- [ ] **Step 5: Run tests, commit**

```bash
pixi run pytest tests/test_e2e_workflow.py -v
git commit -m "test: add crash recovery, resume, cleanup, and question flow e2e tests"
```

---

## Task 11: Interactive Terminal (prompt_toolkit)

Upgrade from simple stdout to split-pane terminal. This is an enhancement over the working simple CLI, not a prerequisite.

**Files:**
- Create: `src/devteam/cli/interactive.py`
- Create: `tests/cli/test_interactive.py`

- [ ] **Step 1: Write tests for command parsing**

Test `parse_command()` for all commands: `/answer Q-1 Use JWT`, `/comment T-3 text`, `/pause`, `/resume`, `/cancel`, `/status`, `/verbose T-1`, `/quiet T-1`, `/priority T-3 high`, `/help`.

- [ ] **Step 2: Implement interactive.py**

Build prompt_toolkit session with:
- `poll_and_render_events()` async task — polls DBOS events from parent + child workflows
- `read_and_dispatch_input()` async task — reads commands, dispatches via `DBOS.send_async()`
- Tier 1 blocking: when question with tier=1 detected, send `control:pause` to all workflows, change prompt to `BLOCKING Q-1> `, wait for answer
- Uses `RuntimeStateStore` for question alias resolution

- [ ] **Step 3: Wire interactive mode into CLI**

Update `job_cmd.py` `start` command to use interactive session when terminal is available, fall back to simple stdout when not.

- [ ] **Step 4: Run tests, commit**

```bash
pixi run pytest tests/cli/test_interactive.py -v
git commit -m "feat: add prompt_toolkit interactive terminal with split-pane UI"
```

---

## Task 12: Remove Old Code

Now that the new DBOS runtime is fully validated, remove the old stopgap modules.

**Files:**
- Delete: `src/devteam/daemon/server.py`, `src/devteam/daemon/process.py`, `src/devteam/daemon/database.py`
- Delete: `src/devteam/orchestrator/cli_bridge.py`, `src/devteam/orchestrator/jobs.py`
- Delete: `src/devteam/concurrency/queue.py`, `src/devteam/concurrency/durable_sleep.py`, `src/devteam/concurrency/invoke.py`
- Modify: `src/devteam/concurrency/__init__.py` (remove deleted re-exports)
- Modify: `src/devteam/concurrency/status_display.py` (remove queue import)
- Modify: `src/devteam/concurrency/cli_priority.py` (remove queue import)
- Modify: `src/devteam/cli/main.py` (remove daemon command registration)
- Delete: corresponding test files

- [ ] **Step 1: Update concurrency/__init__.py**

Remove all re-exports from `queue`, `durable_sleep`, and `invoke` modules. Keep exports from `approval`, `cli_priority` (adapted), `config`, `priority`, `rate_limit` (trimmed), `status_display` (adapted).

- [ ] **Step 2: Update status_display.py**

Remove `from devteam.concurrency.queue import get_active_count`. Replace with a stub or remove `format_queue_status` function.

- [ ] **Step 3: Update cli_priority.py**

Remove `from devteam.concurrency.queue import PENDING`. Replace with a local constant or remove the dependency.

- [ ] **Step 4: Trim rate_limit.py**

Remove `PauseStatus`, `PauseCheckResult`, `init_pause_table`, `set_global_pause`, `get_global_pause`, `clear_global_pause`, `is_paused`, `check_pause_before_invoke`, `handle_rate_limit_error`. Keep `DEFAULT_BACKOFF_SECONDS` and `_parse_reset_seconds` (the only error-parsing function — there is no `parse_retry_after`).

- [ ] **Step 5: Update main.py**

Remove `from devteam.cli.commands import daemon_cmd` and `app.add_typer(daemon_cmd.app, name="daemon")`.

- [ ] **Step 6: Delete source files**

```bash
rm src/devteam/daemon/server.py src/devteam/daemon/process.py src/devteam/daemon/database.py
rm src/devteam/orchestrator/cli_bridge.py src/devteam/orchestrator/jobs.py
rm src/devteam/concurrency/queue.py src/devteam/concurrency/durable_sleep.py src/devteam/concurrency/invoke.py
```

- [ ] **Step 7: Delete test files**

```bash
rm tests/test_daemon.py tests/test_database.py
rm tests/orchestrator/test_cli_bridge.py tests/orchestrator/test_jobs.py
rm tests/concurrency/test_queue.py tests/concurrency/test_durable_sleep.py tests/concurrency/test_rate_limit_invoke.py
```

- [ ] **Step 8: Update remaining test files**

Fix any tests that reference deleted modules. Update `test_rate_limit.py` to only test error parsing.

- [ ] **Step 9: Run full test suite**

Run: `pixi run test`

Expected: All tests pass. No import errors.

- [ ] **Step 10: Commit**

```bash
# Stage specific deleted and modified files (not git add -A)
git rm src/devteam/daemon/server.py src/devteam/daemon/process.py src/devteam/daemon/database.py
git rm src/devteam/orchestrator/cli_bridge.py src/devteam/orchestrator/jobs.py
git rm src/devteam/concurrency/queue.py src/devteam/concurrency/durable_sleep.py src/devteam/concurrency/invoke.py
git rm tests/test_daemon.py tests/test_database.py tests/orchestrator/test_cli_bridge.py tests/orchestrator/test_jobs.py
git rm tests/concurrency/test_queue.py tests/concurrency/test_durable_sleep.py tests/concurrency/test_rate_limit_invoke.py
git add src/devteam/concurrency/__init__.py src/devteam/concurrency/status_display.py
git add src/devteam/concurrency/cli_priority.py src/devteam/concurrency/rate_limit.py
git add src/devteam/cli/main.py
git commit -m "refactor: remove daemon, JobStore, queue, durable_sleep, and invoke stopgaps"
```

---

## Task 13: Final Validation

- [ ] **Step 1: Run full test suite**

Run: `pixi run test`

- [ ] **Step 2: Run formatter, linter, type checker**

```bash
pixi run format
pixi run lint
pixi run typecheck
```

- [ ] **Step 3: Verify deleted files are gone**

```bash
ls src/devteam/daemon/server.py 2>&1 | grep "No such file"
ls src/devteam/orchestrator/cli_bridge.py 2>&1 | grep "No such file"
ls src/devteam/concurrency/queue.py 2>&1 | grep "No such file"
```

- [ ] **Step 4: Final commit**

Stage and commit any remaining fixes from lint/typecheck:

```bash
git add <specific files that were fixed>
git commit -m "chore: final validation — all tests passing, lint clean"
```
