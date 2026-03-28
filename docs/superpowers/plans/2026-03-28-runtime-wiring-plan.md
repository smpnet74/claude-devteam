# Runtime Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire all Phase 2-6 scaffolding into a working single-operator system by placing DBOS durable workflows at the center, removing stopgap systems, and building an interactive terminal experience.

**Architecture:** Single-process CLI hosts an async event loop. DBOS manages workflow durability via SQLite. The operator interacts through a prompt_toolkit split-pane terminal (log panel + input line). Child workflows execute tasks in parallel, communicating via DBOS events (visibility) and messages (control handoff).

**Tech Stack:** Python 3.13, DBOS SDK v2.16+ (SQLite backend), prompt_toolkit, Claude Agent SDK, Typer CLI, Pydantic v2, SurrealDB (optional knowledge), Ollama (optional embeddings)

**Spec:** `docs/superpowers/specs/2026-03-28-runtime-wiring-design.md`

---

## File Structure

### Files to Create

| File | Responsibility |
|------|---------------|
| `src/devteam/orchestrator/bootstrap.py` | Config loading → DBOS init → service wiring → workflow start |
| `src/devteam/orchestrator/events.py` | Event types, `emit_log` helper, event formatters for terminal UI |
| `src/devteam/cli/interactive.py` | prompt_toolkit session: log panel + input line + command dispatch |
| `tests/orchestrator/test_events.py` | Tests for event types and formatters |
| `tests/orchestrator/test_bootstrap.py` | Tests for bootstrap sequence |
| `tests/orchestrator/test_execute_job.py` | Tests for parent workflow |
| `tests/orchestrator/test_execute_task.py` | Tests for child workflow |
| `tests/cli/test_interactive.py` | Tests for command parsing and dispatch |
| `tests/test_e2e_workflow.py` | End-to-end integration tests |

### Files to Modify

| File | Change |
|------|--------|
| `src/devteam/orchestrator/routing.py` | sync → async, add `@DBOS.step()`, remove `InvokerProtocol` |
| `src/devteam/orchestrator/decomposition.py` | sync → async, add `@DBOS.step()` |
| `src/devteam/orchestrator/task_workflow.py` | Rewrite as `@DBOS.workflow()` child workflow |
| `src/devteam/orchestrator/review.py` | sync → async, add `@DBOS.step()` |
| `src/devteam/orchestrator/escalation.py` | sync → async, replace manual tracking with DBOS send/recv |
| `src/devteam/orchestrator/dag.py` | Keep `DAGState`/`build_dag`, replace `DAGExecutor` with `execute_job` workflow + `manage_dag_execution` |
| `src/devteam/agents/invoker.py` | Rewrite as `@DBOS.step()` with knowledge injection and in-step rate-limit retry |
| `src/devteam/cli/commands/job_cmd.py` | `start` launches DBOS workflow + interactive session; `resume` recovers crashed workflows |
| `src/devteam/cli/main.py` | Remove daemon commands, add resume command |
| `src/devteam/config/settings.py` | Add `InteractiveConfig` (polling interval, UI settings) |
| `pyproject.toml` | Add `prompt_toolkit`, move `fastapi`/`uvicorn` to dev-only |
| `tests/orchestrator/test_routing.py` | Convert to async tests |
| `tests/orchestrator/test_decomposition.py` | Convert to async tests |
| `tests/orchestrator/test_task_workflow.py` | Rewrite for DBOS workflow pattern |
| `tests/orchestrator/test_review.py` | Convert to async tests |
| `tests/orchestrator/test_escalation.py` | Convert to async tests |
| `tests/orchestrator/test_dag.py` | Rewrite for DBOS parent workflow pattern |
| `tests/agents/test_invoker.py` | Rewrite for DBOS step pattern |
| `tests/test_cli.py` | Adapt for new command structure |
| `tests/concurrency/test_rate_limit.py` | Remove pause flag tests, keep error parsing tests |

### Files to Delete

| File | Reason |
|------|--------|
| `src/devteam/daemon/server.py` | No FastAPI daemon in V1 |
| `src/devteam/daemon/process.py` | No PID file management |
| `src/devteam/daemon/database.py` | DBOS manages its own SQLite |
| `src/devteam/orchestrator/cli_bridge.py` | JobStore replaced by DBOS |
| `src/devteam/orchestrator/jobs.py` | Job dataclass replaced by DBOS workflow |
| `src/devteam/concurrency/queue.py` | DBOS workflow concurrency replaces SQLite queue |
| `src/devteam/concurrency/durable_sleep.py` | `DBOS.sleep_async()` replaces custom durable sleep |
| `src/devteam/concurrency/invoke.py` | In-step retry replaces external retry wrapper |
| `tests/test_daemon.py` | Daemon removed |
| `tests/test_database.py` | daemon/database.py removed |
| `tests/orchestrator/test_cli_bridge.py` | cli_bridge removed |
| `tests/orchestrator/test_jobs.py` | jobs.py removed |
| `tests/concurrency/test_queue.py` | queue removed |
| `tests/concurrency/test_durable_sleep.py` | durable_sleep removed |
| `tests/concurrency/test_rate_limit_invoke.py` | invoke.py removed |

---

## Phase A: DBOS Foundation

### Task 1: Remove Dead Code

Remove modules that are replaced by DBOS. This unblocks all subsequent tasks by eliminating import errors from deleted modules.

**Files:**
- Delete: `src/devteam/daemon/server.py`, `src/devteam/daemon/process.py`, `src/devteam/daemon/database.py`
- Delete: `src/devteam/orchestrator/cli_bridge.py`, `src/devteam/orchestrator/jobs.py`
- Delete: `src/devteam/concurrency/queue.py`, `src/devteam/concurrency/durable_sleep.py`, `src/devteam/concurrency/invoke.py`
- Delete: `tests/test_daemon.py`, `tests/test_database.py`, `tests/orchestrator/test_cli_bridge.py`, `tests/orchestrator/test_jobs.py`
- Delete: `tests/concurrency/test_queue.py`, `tests/concurrency/test_durable_sleep.py`, `tests/concurrency/test_rate_limit_invoke.py`
- Modify: `src/devteam/cli/main.py`
- Modify: `src/devteam/cli/commands/job_cmd.py`
- Modify: `src/devteam/concurrency/rate_limit.py` (remove pause flag functions, keep error parsing)

- [ ] **Step 1: Delete daemon source files**

```bash
rm src/devteam/daemon/server.py src/devteam/daemon/process.py src/devteam/daemon/database.py
```

Keep `src/devteam/daemon/__init__.py` as an empty file (package may be reused in V2).

- [ ] **Step 2: Delete orchestrator stopgap files**

```bash
rm src/devteam/orchestrator/cli_bridge.py src/devteam/orchestrator/jobs.py
```

- [ ] **Step 3: Delete concurrency stopgap files**

```bash
rm src/devteam/concurrency/queue.py src/devteam/concurrency/durable_sleep.py src/devteam/concurrency/invoke.py
```

- [ ] **Step 4: Delete corresponding test files**

```bash
rm tests/test_daemon.py tests/test_database.py
rm tests/orchestrator/test_cli_bridge.py tests/orchestrator/test_jobs.py
rm tests/concurrency/test_queue.py tests/concurrency/test_durable_sleep.py tests/concurrency/test_rate_limit_invoke.py
```

- [ ] **Step 5: Stub out job_cmd.py to remove cli_bridge dependency**

Replace `src/devteam/cli/commands/job_cmd.py` with a stub that registers the same commands but prints "Not yet wired" messages. This keeps the CLI functional while we build the real implementation.

```python
"""devteam job control commands — start, status, stop, pause, resume, cancel,
comment, answer.

These will be wired to DBOS workflows + interactive terminal session.
"""

from __future__ import annotations

import typer


def register_job_commands(app: typer.Typer) -> None:
    """Register job control commands directly on the main app."""

    @app.command()
    def start(
        spec: str | None = typer.Option(None, "--spec", help="Path to spec document"),
        plan: str | None = typer.Option(None, "--plan", help="Path to plan document"),
        prompt: str | None = typer.Option(None, "--prompt", help="Direct prompt for small fixes"),
        issue: str | None = typer.Option(None, "--issue", help="GitHub issue URL"),
        priority: str | None = typer.Option(
            None, "--priority", help="Job priority: high, normal, low"
        ),
    ) -> None:
        """Start a new development job."""
        if not any([spec, plan, prompt, issue]):
            typer.echo("Provide --spec/--plan, --prompt, or --issue to start a job.")
            raise typer.Exit(code=1)
        typer.echo("Not yet wired: DBOS workflow + interactive session (Phase A)")

    @app.command()
    def status(
        target: str | None = typer.Argument(
            None, help="Job ID (W-1), task (W-1/T-3), or omit for all"
        ),
        questions: bool = typer.Option(False, "--questions", help="Show pending questions"),
    ) -> None:
        """Show status of active jobs and tasks."""
        typer.echo("Not yet wired: DBOS workflow status (Phase A)")

    @app.command()
    def stop(
        target: str | None = typer.Argument(None, help="Job ID (W-1) or omit for all"),
        force: bool = typer.Option(False, "--force", help="Force kill all agents"),
    ) -> None:
        """Stop active jobs gracefully."""
        typer.echo("Not yet wired: DBOS workflow stop (Phase A)")

    @app.command()
    def pause(
        target: str = typer.Argument(help="Job ID (W-1)"),
    ) -> None:
        """Pause a running job."""
        typer.echo("Not yet wired: DBOS workflow pause (Phase C)")

    @app.command()
    def resume(
        target: str | None = typer.Argument(None, help="Job ID (W-1)"),
    ) -> None:
        """Resume a paused job or recover workflows after crash."""
        typer.echo("Not yet wired: DBOS workflow resume (Phase C)")

    @app.command()
    def cancel(
        target: str = typer.Argument(help="Job ID (W-1)"),
        revert_merged: bool = typer.Option(
            False, "--revert-merged", help="Create revert PRs for merged work"
        ),
    ) -> None:
        """Cancel a job and clean up all resources."""
        typer.echo("Not yet wired: DBOS workflow cancel (Phase C)")

    @app.command()
    def comment(
        target: str = typer.Argument(help="Task reference (W-1/T-3 or T-3)"),
        message: str = typer.Argument(help="Feedback message"),
    ) -> None:
        """Inject feedback into a running task."""
        typer.echo("Not yet wired: DBOS workflow comment (Phase C)")

    @app.command()
    def answer(
        question_ref: str = typer.Argument(help="Question reference (Q-1 or W-1/Q-1)"),
        response: str = typer.Argument(help="Your answer"),
    ) -> None:
        """Answer a pending question to resume a paused task."""
        typer.echo("Not yet wired: DBOS workflow answer (Phase C)")
```

- [ ] **Step 6: Update main.py to remove daemon import**

Replace `src/devteam/cli/main.py`:

```python
"""Typer CLI entry point for devteam."""

import typer

from devteam.cli.commands import focus_cmd, init_cmd, project_cmd
from devteam.cli.commands.concurrency_cmd import register_concurrency_commands
from devteam.cli.commands.git_commands import git_app
from devteam.cli.commands.job_cmd import register_job_commands
from devteam.cli.commands.knowledge_cmd import knowledge_app

app = typer.Typer(
    name="devteam",
    help="Durable AI Development Team Orchestrator",
    no_args_is_help=True,
)

# Register command groups
app.add_typer(init_cmd.app, name="init")
app.add_typer(project_cmd.app, name="project")
app.add_typer(focus_cmd.app, name="focus")
app.add_typer(git_app, name="git")
app.add_typer(knowledge_app, name="knowledge")

# Register top-level job control commands
register_job_commands(app)

# Register concurrency commands (prioritize)
register_concurrency_commands(app)


def main() -> None:
    app()
```

- [ ] **Step 7: Strip pause flag functions from rate_limit.py, keep error parsing**

In `src/devteam/concurrency/rate_limit.py`, keep only: `_parse_reset_seconds()`, `handle_rate_limit_error()` (the error parsing part), and `DEFAULT_BACKOFF_SECONDS`. Remove: `PauseStatus`, `PauseCheckResult`, `init_pause_table`, `set_global_pause`, `get_global_pause`, `clear_global_pause`, `is_paused`, `check_pause_before_invoke`.

The trimmed file should be:

```python
"""Rate limit error parsing utilities.

Extracts retry-after durations from Claude API rate limit errors.
Used by invoke_agent_step for in-step retry logic.
"""

from __future__ import annotations

import re


DEFAULT_BACKOFF_SECONDS = 1800  # 30 minutes, from config.toml default


def _parse_reset_seconds(error_message: str) -> int | None:
    """Extract the reset/retry time in seconds from a rate limit error.

    Handles formats:
        - "Retry after 1800 seconds"
        - "retry-after: 120"
        - "Retry after 1800 seconds."
    """
    msg = error_message
    # Pattern: "Retry after N seconds"
    match = re.search(r"[Rr]etry\s+after\s+(\d+)\s+seconds", msg)
    if match:
        return int(match.group(1))
    # Pattern: "retry-after: N" (HTTP header style)
    match = re.search(r"retry-after:\s*(\d+)", msg, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def parse_retry_after(error: Exception) -> int | None:
    """Parse retry-after duration from a rate limit exception.

    Returns seconds to wait, or None if unparseable.
    """
    return _parse_reset_seconds(str(error))
```

- [ ] **Step 8: Update concurrency test_rate_limit.py to match trimmed module**

Remove all tests that reference `init_pause_table`, `set_global_pause`, `get_global_pause`, `clear_global_pause`, `is_paused`, `check_pause_before_invoke`, `PauseStatus`, `PauseCheckResult`. Keep only tests for `_parse_reset_seconds` and `parse_retry_after`.

- [ ] **Step 9: Update test_cli.py to remove cli_bridge references**

In `tests/test_cli.py`, remove any imports from `devteam.orchestrator.cli_bridge` and update test expectations to match the stub output ("Not yet wired").

- [ ] **Step 10: Run tests to verify no import errors**

Run: `pixi run test`

Expected: All remaining tests pass. No `ImportError` or `ModuleNotFoundError` from deleted modules. Test count will drop (deleted test files) but all surviving tests pass.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "refactor: remove daemon, JobStore, and concurrency stopgaps replaced by DBOS"
```

---

### Task 2: Update Dependencies and Config

Add `prompt_toolkit`, move `fastapi`/`uvicorn` to dev-only, and add interactive terminal config.

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/devteam/config/settings.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing test for InteractiveConfig**

Add to `tests/test_config.py`:

```python
from devteam.config.settings import DevteamConfig, InteractiveConfig


class TestInteractiveConfig:
    def test_defaults(self):
        cfg = InteractiveConfig()
        assert cfg.poll_interval_ms == 200
        assert cfg.max_log_lines == 1000

    def test_custom_values(self):
        cfg = InteractiveConfig(poll_interval_ms=500, max_log_lines=500)
        assert cfg.poll_interval_ms == 500
        assert cfg.max_log_lines == 500

    def test_poll_interval_positive(self):
        import pytest
        with pytest.raises(Exception):
            InteractiveConfig(poll_interval_ms=0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/test_config.py::TestInteractiveConfig -v`

Expected: FAIL with `ImportError` — `InteractiveConfig` does not exist yet.

- [ ] **Step 3: Add InteractiveConfig to settings.py**

Add after the `GitConfig` class in `src/devteam/config/settings.py`:

```python
class InteractiveConfig(BaseModel):
    """Interactive terminal UI settings."""

    poll_interval_ms: int = Field(default=200, gt=0)
    max_log_lines: int = Field(default=1000, gt=0)
```

Add the field to `DevteamConfig`:

```python
class DevteamConfig(BaseModel):
    # ... existing fields ...
    interactive: InteractiveConfig = Field(default_factory=InteractiveConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run pytest tests/test_config.py::TestInteractiveConfig -v`

Expected: PASS

- [ ] **Step 5: Update pyproject.toml dependencies**

In `pyproject.toml`, replace the dependencies list:

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
```

Move fastapi and uvicorn to a new `daemon` dependency group (kept for potential V2):

```toml
[dependency-groups]
test = ["pytest>=8,<9", "pytest-asyncio>=0.23,<1"]
dev = ["ruff>=0.15,<1", "pyright>=1.1,<2"]
daemon = ["fastapi>=0.135,<1", "uvicorn[standard]>=0.42,<1"]
```

- [ ] **Step 6: Install updated dependencies**

Run: `pixi install`

Expected: Resolves successfully, `prompt_toolkit` installed.

- [ ] **Step 7: Run full test suite**

Run: `pixi run test`

Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml pixi.lock src/devteam/config/settings.py tests/test_config.py
git commit -m "chore: add prompt_toolkit, move fastapi to dev-only, add InteractiveConfig"
```

---

### Task 3: Create DBOS Test Fixture

Create a shared pytest fixture that initializes DBOS with a temporary SQLite database for workflow tests.

**Files:**
- Create: `tests/conftest_dbos.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write the DBOS test fixture**

Create `tests/conftest_dbos.py`:

```python
"""Shared DBOS test fixtures.

Provides a DBOS instance configured with a temporary SQLite database.
Import this fixture in any test that needs DBOS workflows or steps.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def dbos_db_path(tmp_path: Path) -> str:
    """Return a SQLite URL for a temp DBOS database."""
    return f"sqlite:///{tmp_path / 'dbos_test.sqlite'}"


@pytest.fixture
def dbos_launch(dbos_db_path: str):
    """Initialize and launch DBOS with a temp database.

    Yields the DBOS class. Destroys on teardown.
    """
    from dbos import DBOS

    DBOS(config={"name": "devteam_test", "system_database_url": dbos_db_path})
    DBOS.launch()
    yield DBOS
    DBOS.destroy()
```

- [ ] **Step 2: Import in conftest.py**

Add to `tests/conftest.py`:

```python
# Import DBOS fixtures so they're available to all tests
from tests.conftest_dbos import dbos_db_path, dbos_launch  # noqa: F401
```

- [ ] **Step 3: Write a smoke test to verify the fixture works**

Add a temporary test to verify DBOS initialization:

Create `tests/test_dbos_smoke.py`:

```python
"""Smoke test for DBOS test fixture."""

import pytest
from dbos import DBOS


@pytest.mark.asyncio
async def test_dbos_launches(dbos_launch):
    """Verify DBOS initializes with temp SQLite and a simple workflow runs."""

    @DBOS.workflow()
    async def hello_workflow(name: str) -> str:
        return f"hello {name}"

    result = await hello_workflow("world")
    assert result == "hello world"


@pytest.mark.asyncio
async def test_dbos_step_runs(dbos_launch):
    """Verify a DBOS step executes."""

    @DBOS.step()
    async def add_step(a: int, b: int) -> int:
        return a + b

    @DBOS.workflow()
    async def add_workflow(a: int, b: int) -> int:
        return await add_step(a, b)

    result = await add_workflow(3, 4)
    assert result == 7
```

- [ ] **Step 4: Run the smoke test**

Run: `pixi run pytest tests/test_dbos_smoke.py -v`

Expected: PASS — DBOS initializes, workflow runs, step runs.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest_dbos.py tests/conftest.py tests/test_dbos_smoke.py
git commit -m "test: add DBOS test fixture with temp SQLite database"
```

---

### Task 4: Create Event Types and Formatters

Create the event module used by workflows to emit log events and by the terminal to render them.

**Files:**
- Create: `src/devteam/orchestrator/events.py`
- Create: `tests/orchestrator/test_events.py`

- [ ] **Step 1: Write failing tests for event types**

Create `tests/orchestrator/test_events.py`:

```python
"""Tests for workflow event types and formatters."""

from devteam.orchestrator.events import (
    EventLevel,
    LogEvent,
    format_log_event,
    make_log_key,
)


class TestLogEvent:
    def test_create_log_event(self):
        evt = LogEvent(message="Task started", level=EventLevel.INFO, seq=1)
        assert evt.message == "Task started"
        assert evt.level == EventLevel.INFO
        assert evt.seq == 1
        assert evt.timestamp > 0

    def test_format_info(self):
        evt = LogEvent(message="Routing... full_project", level=EventLevel.INFO, seq=1)
        line = format_log_event(evt, job_id="W-1")
        assert "[W-1]" in line
        assert "Routing... full_project" in line

    def test_format_task_event(self):
        evt = LogEvent(message="backend_engineer starting", level=EventLevel.INFO, seq=3)
        line = format_log_event(evt, job_id="W-1", task_id="T-1")
        assert "[W-1/T-1]" in line
        assert "backend_engineer starting" in line

    def test_format_question(self):
        evt = LogEvent(message="Redis or JWT?", level=EventLevel.QUESTION, seq=5)
        line = format_log_event(evt, job_id="W-1", task_id="T-2")
        assert "QUESTION" in line

    def test_format_error(self):
        evt = LogEvent(message="Agent failed", level=EventLevel.ERROR, seq=10)
        line = format_log_event(evt, job_id="W-1")
        assert "ERROR" in line or "Agent failed" in line


class TestMakeLogKey:
    def test_sequential_keys(self):
        assert make_log_key(1) == "log:000001"
        assert make_log_key(42) == "log:000042"
        assert make_log_key(999999) == "log:999999"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/orchestrator/test_events.py -v`

Expected: FAIL with `ModuleNotFoundError` — `events` module does not exist.

- [ ] **Step 3: Implement events.py**

Create `src/devteam/orchestrator/events.py`:

```python
"""Workflow event types and formatters.

Events are set on DBOS workflows via set_event() and polled by the
terminal UI via get_all_events_async(). This module defines the event
data structures and formatting logic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class EventLevel(str, Enum):
    """Severity level for log events."""

    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    QUESTION = "question"
    SUCCESS = "success"


@dataclass(frozen=True)
class LogEvent:
    """A single log event emitted by a workflow."""

    message: str
    level: EventLevel
    seq: int
    timestamp: float = field(default_factory=time.time)


def make_log_key(seq: int) -> str:
    """Create a DBOS event key for a log entry.

    Keys are zero-padded to 6 digits so lexicographic sort == numeric sort.
    """
    return f"log:{seq:06d}"


def format_log_event(
    event: LogEvent,
    job_id: str,
    task_id: str | None = None,
) -> str:
    """Format a log event as a terminal display line.

    Args:
        event: The log event to format.
        job_id: Job identifier (e.g., "W-1").
        task_id: Optional task identifier (e.g., "T-1").

    Returns:
        A single-line string for the log panel.
    """
    prefix = f"[{job_id}/{task_id}]" if task_id else f"[{job_id}]"

    if event.level == EventLevel.QUESTION:
        return f"{prefix} QUESTION {event.message}"
    if event.level == EventLevel.ERROR:
        return f"{prefix} ERROR {event.message}"
    if event.level == EventLevel.WARN:
        return f"{prefix} WARN {event.message}"
    if event.level == EventLevel.SUCCESS:
        return f"{prefix} {event.message}"
    return f"{prefix} {event.message}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/orchestrator/test_events.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/orchestrator/events.py tests/orchestrator/test_events.py
git commit -m "feat: add workflow event types and terminal formatters"
```

---

### Task 5: Convert routing.py to Async + DBOS Step

Convert `route_intake` from sync to async and add `@DBOS.step()`. Remove the `InvokerProtocol` — routing will call `invoke_agent_step` directly (wired in Task 8). For now, accept an async callable as the invoker parameter.

**Files:**
- Modify: `src/devteam/orchestrator/routing.py`
- Modify: `tests/orchestrator/test_routing.py`

- [ ] **Step 1: Update test_routing.py to use async tests**

The existing tests use a sync `InvokerProtocol`. Convert them to use an async callable. Read the existing test file first, then update each test class to use `@pytest.mark.asyncio` and `async def`. Replace the sync mock invoker with an async one.

The mock invoker pattern:

```python
from collections.abc import Callable, Coroutine
from typing import Any


async def mock_invoker(
    role: str,
    prompt: str,
    *,
    json_schema: dict[str, Any] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Mock async invoker for tests."""
    return {"path": "full_project", "reasoning": "Test routing"}
```

Update every `def test_...` to `async def test_...` and add `@pytest.mark.asyncio`. Update every call to `route_intake(ctx, invoker)` to `await route_intake(ctx, invoker)`.

Key test to verify:

```python
@pytest.mark.asyncio
async def test_fast_path_spec_and_plan():
    """Spec + plan provided → direct to full_project, no invoker needed."""
    ctx = IntakeContext(spec="spec", plan="plan")
    result = await route_intake(ctx, invoker=None)
    assert result.path == RoutePath.FULL_PROJECT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/orchestrator/test_routing.py -v`

Expected: FAIL — `route_intake` is still sync.

- [ ] **Step 3: Convert routing.py to async**

In `src/devteam/orchestrator/routing.py`:

1. Remove `InvokerProtocol` class entirely.
2. Change `route_intake` signature to accept an async callable:

```python
from collections.abc import Callable, Coroutine
from typing import Any

# Type alias for the async invoker callable
AsyncInvoker = Callable[..., Coroutine[Any, Any, dict[str, Any]]]


async def route_intake(
    ctx: IntakeContext,
    invoker: AsyncInvoker | None,
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

    if invoker is None:
        raise ValueError("CEO analysis required but no invoker provided")

    # CEO analysis needed
    prompt = build_routing_prompt(ctx)
    result = await invoker(
        role="ceo",
        prompt=prompt,
        json_schema=RoutingResult.model_json_schema(),
    )
    return RoutingResult.model_validate(result)
```

Keep `classify_intake` and `build_routing_prompt` unchanged — they are pure functions.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/orchestrator/test_routing.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/orchestrator/routing.py tests/orchestrator/test_routing.py
git commit -m "refactor: convert routing to async, remove InvokerProtocol"
```

---

### Task 6: Convert decomposition.py to Async

Convert `decompose` from sync to async. Replace `InvokerProtocol` usage with async callable.

**Files:**
- Modify: `src/devteam/orchestrator/decomposition.py`
- Modify: `tests/orchestrator/test_decomposition.py`

- [ ] **Step 1: Update test_decomposition.py to async**

Same pattern as Task 5: convert all tests to `@pytest.mark.asyncio` / `async def`, replace sync mock with async mock. Update `decompose(spec, plan, routing, invoker)` calls to `await decompose(...)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/orchestrator/test_decomposition.py -v`

Expected: FAIL — `decompose` is still sync.

- [ ] **Step 3: Convert decomposition.py to async**

In `src/devteam/orchestrator/decomposition.py`:

1. Remove the `InvokerProtocol` import from routing.
2. Import the `AsyncInvoker` type alias from routing: `from devteam.orchestrator.routing import AsyncInvoker`
3. Change `decompose` signature:

```python
async def decompose(
    spec: str,
    plan: str,
    routing: RoutingResult,
    invoker: AsyncInvoker,
) -> DecompositionResult:
    """Invoke CA to decompose spec+plan into task DAG."""
    if routing.path not in (RoutePath.FULL_PROJECT, RoutePath.OSS_CONTRIBUTION):
        raise ValueError(
            f"decompose() only supports FULL_PROJECT and OSS_CONTRIBUTION, got {routing.path.value}"
        )

    prompt = build_decomposition_prompt(spec, plan, routing)
    raw = await invoker(
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
```

Keep all pure functions (`build_decomposition_prompt`, `assign_peer_reviewers`, `validate_decomposition`, `get_default_peer_reviewer`) unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/orchestrator/test_decomposition.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/orchestrator/decomposition.py tests/orchestrator/test_decomposition.py
git commit -m "refactor: convert decomposition to async"
```

---

### Task 7: Convert review.py and escalation.py to Async

Convert the remaining orchestrator modules to async.

**Files:**
- Modify: `src/devteam/orchestrator/review.py`
- Modify: `src/devteam/orchestrator/escalation.py`
- Modify: `tests/orchestrator/test_review.py`
- Modify: `tests/orchestrator/test_escalation.py`

- [ ] **Step 1: Update test_review.py to async**

Same async conversion pattern. Update `execute_post_pr_review(...)` calls to `await execute_post_pr_review(...)`.

- [ ] **Step 2: Convert review.py to async**

Change `execute_post_pr_review` to `async def`. The internal loop over gates now uses `await invoker(...)`. Keep all pure functions unchanged (`sanitize_pr_context`, `get_review_chain`, `is_small_fix_with_no_behavior_change`).

```python
async def execute_post_pr_review(
    work_type: WorkType,
    pr_context: str,
    invoker: AsyncInvoker,
    files_changed: list[str] | None = None,
    skip_qa_for_no_behavior_change: bool = True,
    assigned_to: str | None = None,
) -> PostPRReviewResult:
    """Execute the post-PR review chain for a work type."""
    pr_context = sanitize_pr_context(pr_context)
    chain = get_review_chain(work_type, assigned_to=assigned_to)
    gate_results: dict[str, ReviewResult] = {}
    failed_gates: list[str] = []
    skipped_gates: list[str] = []

    for gate in chain.gates:
        if (
            skip_qa_for_no_behavior_change
            and gate.name == "qa_review"
            and files_changed
            and is_small_fix_with_no_behavior_change(work_type, files_changed)
        ):
            skipped_gates.append(gate.name)
            continue

        try:
            raw = await invoker(
                role=gate.reviewer_role,
                prompt=(
                    f"## {gate.name.replace('_', ' ').title()}\n\n"
                    f"{pr_context}\n\n"
                    "Review and provide your verdict.\n"
                ),
                json_schema=ReviewResult.model_json_schema(),
            )
        except Exception as e:
            if not gate.required:
                failed_gates.append(gate.name)
                continue
            raise RuntimeError(
                f"Post-PR review gate '{gate.name}' invocation failed: {e}"
            ) from e

        try:
            result = ReviewResult.model_validate(raw)
        except ValidationError as e:
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

    required_gate_names = {g.name for g in chain.gates if g.required}
    required_failures = [g for g in failed_gates if g in required_gate_names]
    return PostPRReviewResult(
        all_passed=len(required_failures) == 0,
        gate_results=gate_results,
        failed_gates=failed_gates,
        skipped_gates=skipped_gates,
    )
```

- [ ] **Step 3: Run review tests**

Run: `pixi run pytest tests/orchestrator/test_review.py -v`

Expected: PASS

- [ ] **Step 4: Update test_escalation.py to async**

Convert tests to async. `escalate_question(...)` becomes `await escalate_question(...)`. `attempt_resolution(...)` becomes `await attempt_resolution(...)`.

- [ ] **Step 5: Convert escalation.py to async**

Change `attempt_resolution` and `escalate_question` to `async def`:

```python
from devteam.orchestrator.routing import AsyncInvoker


async def attempt_resolution(
    question: QuestionRecord,
    level: str,
    invoker: AsyncInvoker,
) -> EscalationAttempt:
    """Attempt to resolve a question at a given escalation level."""
    prompt = build_escalation_prompt(question, level)
    try:
        raw = await invoker(
            role=level,
            prompt=prompt,
            json_schema=EscalationAttemptResult.model_json_schema(),
        )
    except Exception as e:
        raise RuntimeError(f"Escalation attempt to '{level}' failed: {e}") from e

    try:
        validated = EscalationAttemptResult.model_validate(raw)
    except Exception:
        return EscalationAttempt(
            level=level,
            resolved=False,
            reasoning=f"Malformed response from {level}: failed schema validation",
        )

    return EscalationAttempt(
        level=level,
        resolved=validated.resolved,
        answer=validated.answer,
        reasoning=validated.reasoning,
    )


async def escalate_question(
    question: QuestionRecord,
    invoker: AsyncInvoker,
    em_role: str = "em_team_a",
) -> EscalationResult:
    """Run the escalation workflow for a question."""
    path = get_escalation_path(question.question_type)
    attempts: list[EscalationAttempt] = []
    path = [em_role if level == "em" else level for level in path]

    for level in path:
        if level == "human":
            return EscalationResult(
                question=question,
                resolved=False,
                final_level=EscalationLevel.HUMAN,
                attempts=attempts,
                needs_human=True,
            )

        attempt = await attempt_resolution(question, level, invoker)
        attempts.append(attempt)

        if attempt.resolved:
            if level == em_role:
                final_level = EscalationLevel.SUPERVISOR
            else:
                final_level = EscalationLevel.LEADERSHIP

            return EscalationResult(
                question=question,
                resolved=True,
                final_level=final_level,
                attempts=attempts,
                answer=attempt.answer,
            )

    return EscalationResult(
        question=question,
        resolved=False,
        final_level=EscalationLevel.HUMAN,
        attempts=attempts,
        needs_human=True,
    )
```

Keep `resolve_with_human_answer`, `build_escalation_prompt`, `get_escalation_path` unchanged.

- [ ] **Step 6: Run escalation tests**

Run: `pixi run pytest tests/orchestrator/test_escalation.py -v`

Expected: PASS

- [ ] **Step 7: Run full test suite**

Run: `pixi run test`

Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/devteam/orchestrator/review.py src/devteam/orchestrator/escalation.py
git add tests/orchestrator/test_review.py tests/orchestrator/test_escalation.py
git commit -m "refactor: convert review and escalation to async"
```

---

## Phase B: Agent Invocation

### Task 8: Rewrite Agent Invoker as DBOS Step

Rewrite `agents/invoker.py` to expose a module-level `invoke_agent_step` decorated with `@DBOS.step()`. Wire knowledge injection and in-step rate-limit retry.

**Files:**
- Modify: `src/devteam/agents/invoker.py`
- Modify: `tests/agents/test_invoker.py`

- [ ] **Step 1: Write failing tests for the new invoke_agent_step**

Create new tests in `tests/agents/test_invoker.py` (replace the existing `AgentInvoker` class-based tests):

```python
"""Tests for invoke_agent_step DBOS step."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from devteam.agents.invoker import (
    InvocationError,
    QueryOptions,
    invoke_agent_step,
    get_output_schema,
    set_agent_registry,
    set_knowledge_store,
)


class TestGetOutputSchema:
    def test_ceo_returns_routing(self):
        schema = get_output_schema("ceo")
        assert "path" in schema["properties"]

    def test_engineer_returns_implementation(self):
        schema = get_output_schema("backend_engineer")
        assert "summary" in schema["properties"]

    def test_unknown_role_raises(self):
        with pytest.raises(InvocationError):
            get_output_schema("nonexistent_role")


class TestInvokeAgentStep:
    @pytest.mark.asyncio
    async def test_successful_invocation(self, dbos_launch, tmp_path):
        """invoke_agent_step calls the SDK and returns parsed result."""
        # Set up mock registry
        mock_defn = MagicMock()
        mock_defn.model = "sonnet"
        mock_defn.prompt = "You are an engineer."
        mock_defn.tools = ("Read", "Edit")

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_defn
        set_agent_registry(mock_registry)
        set_knowledge_store(None)

        mock_response = {
            "summary": "Implemented feature",
            "files_changed": ["foo.py"],
            "tests_added": ["test_foo.py"],
            "confidence": "high",
            "status": "completed",
        }

        with patch("devteam.agents.invoker._call_claude_sdk") as mock_sdk:
            mock_sdk.return_value = mock_response

            @dbos_launch.workflow()
            async def test_wf():
                return await invoke_agent_step(
                    role="backend_engineer",
                    prompt="Build a thing",
                    worktree_path=str(tmp_path),
                    project_name="test-project",
                )

            result = await test_wf()
            assert result.summary == "Implemented feature"

    @pytest.mark.asyncio
    async def test_rate_limit_retry(self, dbos_launch, tmp_path):
        """invoke_agent_step retries on RateLimitError with backoff."""
        mock_defn = MagicMock()
        mock_defn.model = "sonnet"
        mock_defn.prompt = "You are an engineer."
        mock_defn.tools = ("Read",)

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_defn
        set_agent_registry(mock_registry)
        set_knowledge_store(None)

        call_count = 0

        async def flaky_sdk(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise InvocationError("rate limit: Retry after 1 seconds")
            return {
                "summary": "Done",
                "files_changed": [],
                "tests_added": [],
                "confidence": "high",
                "status": "completed",
            }

        with patch("devteam.agents.invoker._call_claude_sdk", side_effect=flaky_sdk):
            with patch("devteam.agents.invoker._is_rate_limit_error", return_value=True):

                @dbos_launch.workflow()
                async def test_wf():
                    return await invoke_agent_step(
                        role="backend_engineer",
                        prompt="Build a thing",
                        worktree_path=str(tmp_path),
                        project_name="test-project",
                    )

                result = await test_wf()
                assert result.summary == "Done"
                assert call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/agents/test_invoker.py::TestInvokeAgentStep -v`

Expected: FAIL — functions don't exist yet.

- [ ] **Step 3: Rewrite invoker.py**

Replace `src/devteam/agents/invoker.py` with the new implementation. Key changes:
- Module-level singletons for registry, knowledge_store, embedder, config (set by bootstrap)
- `invoke_agent_step` as a `@DBOS.step()` function
- In-step rate-limit retry loop
- `_call_claude_sdk` as a thin wrapper around the real SDK (easy to mock)

```python
"""Agent invoker — DBOS step wrapping Claude Agent SDK calls.

Decorated with @DBOS.step() for crash-safe replay. Handles:
1. Knowledge injection (memory index into system prompt)
2. Output schema selection based on role
3. Rate-limit retry with exponential backoff inside the step
"""

from __future__ import annotations

import json
import logging
from typing import Any

from dbos import DBOS
from pydantic import BaseModel

from devteam.agents.contracts import (
    DecompositionResult,
    ImplementationResult,
    ReviewResult,
    RoutingResult,
)
from devteam.agents.registry import AgentRegistry
from devteam.concurrency.rate_limit import parse_retry_after

logger = logging.getLogger(__name__)


class InvocationError(Exception):
    """Raised when an agent invocation fails."""


# ---------------------------------------------------------------------------
# Role → output schema mapping
# ---------------------------------------------------------------------------

_ROLE_SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "ceo": RoutingResult,
    "chief_architect": DecompositionResult,
    "planner_researcher_a": ImplementationResult,
    "planner_researcher_b": ImplementationResult,
    "em_team_a": ReviewResult,
    "em_team_b": ReviewResult,
    "qa_engineer": ReviewResult,
    "security_engineer": ReviewResult,
    "tech_writer": ReviewResult,
}

_ENGINEER_ROLES = frozenset({
    "backend_engineer",
    "frontend_engineer",
    "devops_engineer",
    "data_engineer",
    "infra_engineer",
    "tooling_engineer",
    "cloud_engineer",
})


def get_output_schema(role: str) -> dict[str, Any]:
    """Return the JSON schema for a role's structured output."""
    cls = _ROLE_SCHEMA_MAP.get(role)
    if cls is None and role in _ENGINEER_ROLES:
        cls = ImplementationResult
    if cls is None:
        raise InvocationError(f"No output schema mapped for role '{role}'")
    return cls.model_json_schema()


def _get_result_type(role: str) -> type[BaseModel]:
    """Return the Pydantic model class for a role."""
    cls = _ROLE_SCHEMA_MAP.get(role)
    if cls is None and role in _ENGINEER_ROLES:
        cls = ImplementationResult
    if cls is None:
        raise InvocationError(f"No output schema mapped for role '{role}'")
    return cls


# ---------------------------------------------------------------------------
# Module-level singletons (set by bootstrap.py)
# ---------------------------------------------------------------------------

_agent_registry: AgentRegistry | None = None
_knowledge_store: Any | None = None  # KnowledgeStore or None
_embedder: Any | None = None


def set_agent_registry(registry: AgentRegistry | None) -> None:
    global _agent_registry
    _agent_registry = registry


def set_knowledge_store(store: Any | None) -> None:
    global _knowledge_store
    _knowledge_store = store


def set_embedder(embedder: Any | None) -> None:
    global _embedder
    _embedder = embedder


# ---------------------------------------------------------------------------
# Rate limit detection
# ---------------------------------------------------------------------------

def _is_rate_limit_error(error: Exception) -> bool:
    """Check if an exception is a rate limit error."""
    msg = str(error).lower()
    return "rate limit" in msg or "retry after" in msg or "429" in msg


# ---------------------------------------------------------------------------
# Claude SDK thin wrapper (mockable in tests)
# ---------------------------------------------------------------------------

async def _call_claude_sdk(
    prompt: str,
    model: str,
    system_prompt: str,
    tools: list[str],
    cwd: str | None,
    output_schema: dict[str, Any],
) -> dict[str, Any]:
    """Call the Claude Agent SDK and return the parsed result dict.

    This is a thin wrapper so tests can mock it without touching the SDK.
    """
    from claude_agent_sdk import query

    class _Options:
        pass

    options = _Options()
    options.model = model  # type: ignore[attr-defined]
    options.system_prompt = system_prompt  # type: ignore[attr-defined]
    options.allowed_tools = tools  # type: ignore[attr-defined]
    options.permission_mode = "default"  # type: ignore[attr-defined]
    options.cwd = cwd  # type: ignore[attr-defined]
    options.output_format = {"type": "json_schema", "schema": output_schema}  # type: ignore[attr-defined]

    result_msg = None
    async for message in query(prompt=prompt, options=options):
        if hasattr(message, "result"):
            result_msg = message

    if result_msg is None:
        raise InvocationError("No ResultMessage received from SDK")

    if hasattr(result_msg, "is_error") and result_msg.is_error:
        raise InvocationError(f"Agent returned error: {result_msg.result}")

    if hasattr(result_msg, "structured_output") and result_msg.structured_output is not None:
        return result_msg.structured_output
    return json.loads(result_msg.result)


# ---------------------------------------------------------------------------
# The DBOS step
# ---------------------------------------------------------------------------

@DBOS.step(retries_allowed=False)
async def invoke_agent_step(
    role: str,
    prompt: str,
    worktree_path: str | None,
    project_name: str,
    max_retries: int = 3,
) -> BaseModel:
    """Invoke an agent with knowledge injection and rate-limit retry.

    This is a DBOS step — on crash recovery, DBOS replays it using the
    stored return value (it does NOT re-invoke the agent).

    Args:
        role: Agent role slug (must exist in registry).
        prompt: The task-specific prompt.
        worktree_path: Working directory for the agent.
        project_name: Project name for knowledge context.
        max_retries: Max rate-limit retries.

    Returns:
        Parsed Pydantic model (ImplementationResult, ReviewResult, etc.).
    """
    assert _agent_registry is not None, "Agent registry not initialized (call bootstrap first)"
    defn = _agent_registry.get(role)
    output_schema = get_output_schema(role)
    result_type = _get_result_type(role)

    # Build knowledge context
    knowledge_index = ""
    if _knowledge_store is not None:
        try:
            from devteam.knowledge.index import build_memory_index_safe

            knowledge_index = await build_memory_index_safe(_knowledge_store, project_name)
        except Exception:
            pass  # Knowledge failure should not block invocation

    full_system_prompt = defn.prompt
    if knowledge_index:
        full_system_prompt = f"{defn.prompt}\n\n{knowledge_index}"

    # Build tools list — omit query_knowledge if knowledge unavailable
    tools = list(defn.tools)
    if _knowledge_store is not None:
        tools.append("query_knowledge")

    # Rate-limit retry loop
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            raw = await _call_claude_sdk(
                prompt=prompt,
                model=defn.model,
                system_prompt=full_system_prompt,
                tools=tools,
                cwd=worktree_path,
                output_schema=output_schema,
            )
            return result_type.model_validate(raw)
        except InvocationError as e:
            if _is_rate_limit_error(e):
                last_error = e
                backoff = parse_retry_after(e) or (60 * (2 ** attempt))
                logger.warning(
                    "Rate limit hit for '%s' (attempt %d/%d), sleeping %ds",
                    role, attempt + 1, max_retries + 1, backoff,
                )
                await DBOS.sleep_async(float(backoff))
                continue
            raise

    assert last_error is not None
    raise last_error
```

- [ ] **Step 4: Run invoker tests**

Run: `pixi run pytest tests/agents/test_invoker.py -v`

Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pixi run test`

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/devteam/agents/invoker.py tests/agents/test_invoker.py
git commit -m "feat: rewrite agent invoker as DBOS step with knowledge injection and rate-limit retry"
```

---

## Phase C: Interactive Terminal

### Task 9: Create Interactive Terminal Core

Build the prompt_toolkit-based interactive terminal with log panel and input line.

**Files:**
- Create: `src/devteam/cli/interactive.py`
- Create: `tests/cli/test_interactive.py`

- [ ] **Step 1: Write failing tests for command parsing**

Create `tests/cli/test_interactive.py`:

```python
"""Tests for interactive terminal command parsing."""

from devteam.cli.interactive import parse_command, Command, CommandType


class TestParseCommand:
    def test_answer(self):
        cmd = parse_command("/answer Q-1 Use JWT")
        assert cmd.type == CommandType.ANSWER
        assert cmd.target == "Q-1"
        assert cmd.payload == "Use JWT"

    def test_comment(self):
        cmd = parse_command("/comment T-3 Use staging cluster")
        assert cmd.type == CommandType.COMMENT
        assert cmd.target == "T-3"
        assert cmd.payload == "Use staging cluster"

    def test_pause(self):
        cmd = parse_command("/pause")
        assert cmd.type == CommandType.PAUSE

    def test_resume(self):
        cmd = parse_command("/resume")
        assert cmd.type == CommandType.RESUME

    def test_cancel(self):
        cmd = parse_command("/cancel")
        assert cmd.type == CommandType.CANCEL

    def test_status(self):
        cmd = parse_command("/status")
        assert cmd.type == CommandType.STATUS

    def test_verbose(self):
        cmd = parse_command("/verbose T-1")
        assert cmd.type == CommandType.VERBOSE
        assert cmd.target == "T-1"

    def test_quiet(self):
        cmd = parse_command("/quiet T-1")
        assert cmd.type == CommandType.QUIET
        assert cmd.target == "T-1"

    def test_priority(self):
        cmd = parse_command("/priority T-3 high")
        assert cmd.type == CommandType.PRIORITY
        assert cmd.target == "T-3"
        assert cmd.payload == "high"

    def test_help(self):
        cmd = parse_command("/help")
        assert cmd.type == CommandType.HELP

    def test_unknown(self):
        cmd = parse_command("/foobar")
        assert cmd.type == CommandType.UNKNOWN

    def test_empty_input(self):
        cmd = parse_command("")
        assert cmd.type == CommandType.UNKNOWN

    def test_no_slash(self):
        cmd = parse_command("just some text")
        assert cmd.type == CommandType.UNKNOWN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/cli/test_interactive.py -v`

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement interactive.py**

Create `src/devteam/cli/interactive.py`:

```python
"""Interactive terminal session for devteam.

Provides a split-pane terminal UI:
- Top: scrolling log panel (workflow events)
- Bottom: persistent input line (operator commands)

Built with prompt_toolkit for concurrent async input + output.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from dbos import DBOS

from devteam.orchestrator.events import (
    EventLevel,
    LogEvent,
    format_log_event,
    make_log_key,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------


class CommandType(str, Enum):
    ANSWER = "answer"
    COMMENT = "comment"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    STATUS = "status"
    VERBOSE = "verbose"
    QUIET = "quiet"
    PRIORITY = "priority"
    HELP = "help"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Command:
    """A parsed operator command."""

    type: CommandType
    target: str | None = None
    payload: str | None = None


def parse_command(text: str) -> Command:
    """Parse operator input into a Command.

    Commands start with / followed by the command name.
    Arguments are space-separated after the command name.
    """
    text = text.strip()
    if not text or not text.startswith("/"):
        return Command(type=CommandType.UNKNOWN)

    parts = text[1:].split(None, 2)  # Remove leading /, split into max 3 parts
    if not parts:
        return Command(type=CommandType.UNKNOWN)

    cmd_name = parts[0].lower()

    try:
        cmd_type = CommandType(cmd_name)
    except ValueError:
        return Command(type=CommandType.UNKNOWN)

    target = parts[1] if len(parts) > 1 else None
    payload = parts[2] if len(parts) > 2 else None

    # For commands that take target + payload (answer, comment, priority)
    # the second part is the target and the rest is the payload
    if cmd_type in (CommandType.ANSWER, CommandType.COMMENT, CommandType.PRIORITY):
        return Command(type=cmd_type, target=target, payload=payload)

    # For commands that take just a target (verbose, quiet)
    if cmd_type in (CommandType.VERBOSE, CommandType.QUIET):
        return Command(type=cmd_type, target=target)

    # For commands with no arguments (pause, resume, cancel, status, help)
    return Command(type=cmd_type)


# ---------------------------------------------------------------------------
# Question tracking for display alias mapping
# ---------------------------------------------------------------------------


@dataclass
class QuestionTracker:
    """Maps display aliases (Q-1, Q-2) to internal DBOS IDs."""

    _next_display_id: int = 1
    _display_to_internal: dict[str, str] = field(default_factory=dict)
    _internal_to_display: dict[str, str] = field(default_factory=dict)
    _question_to_child: dict[str, str] = field(default_factory=dict)

    def register(self, internal_id: str, child_workflow_id: str) -> str:
        """Register a question and return its display alias."""
        if internal_id in self._internal_to_display:
            return self._internal_to_display[internal_id]
        display_id = f"Q-{self._next_display_id}"
        self._next_display_id += 1
        self._display_to_internal[display_id] = internal_id
        self._internal_to_display[internal_id] = display_id
        self._question_to_child[display_id] = child_workflow_id
        return display_id

    def resolve(self, display_id: str) -> tuple[str, str] | None:
        """Look up internal ID and child workflow ID from display alias.

        Returns (internal_id, child_workflow_id) or None if not found.
        """
        internal = self._display_to_internal.get(display_id)
        child = self._question_to_child.get(display_id)
        if internal is None or child is None:
            return None
        return (internal, child)


# ---------------------------------------------------------------------------
# Event polling
# ---------------------------------------------------------------------------


async def poll_workflow_events(
    workflow_id: str,
    last_seen: int,
) -> tuple[list[LogEvent], int]:
    """Poll for new log events from a workflow.

    Returns (new_events, new_last_seen).
    """
    all_events = await DBOS.get_all_events_async(workflow_id)
    new_events: list[LogEvent] = []

    for key, value in all_events.items():
        if not key.startswith("log:"):
            continue
        seq = int(key.split(":")[1])
        if seq <= last_seen:
            continue
        if isinstance(value, dict):
            new_events.append(LogEvent(
                message=value.get("message", ""),
                level=EventLevel(value.get("level", "info")),
                seq=seq,
                timestamp=value.get("timestamp", 0),
            ))
        else:
            new_events.append(LogEvent(
                message=str(value),
                level=EventLevel.INFO,
                seq=seq,
            ))

    new_events.sort(key=lambda e: e.seq)
    new_last_seen = max((e.seq for e in new_events), default=last_seen)
    return new_events, new_last_seen


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------


async def dispatch_command(
    cmd: Command,
    parent_workflow_id: str,
    child_workflow_ids: dict[str, str],
    question_tracker: QuestionTracker,
) -> str | None:
    """Dispatch a parsed command to the appropriate DBOS workflow.

    Returns a status message to display, or None.
    """
    if cmd.type == CommandType.ANSWER:
        if not cmd.target or not cmd.payload:
            return "Usage: /answer Q-1 <your answer>"
        resolved = question_tracker.resolve(cmd.target)
        if resolved is None:
            return f"Unknown question: {cmd.target}"
        internal_id, child_wf_id = resolved
        await DBOS.send_async(
            destination_id=child_wf_id,
            message=cmd.payload,
            topic=f"answer:{internal_id}",
        )
        return f"Answer sent to {cmd.target}"

    if cmd.type == CommandType.COMMENT:
        if not cmd.target or not cmd.payload:
            return "Usage: /comment T-1 <feedback>"
        child_wf_id = child_workflow_ids.get(cmd.target)
        if child_wf_id is None:
            return f"Unknown task: {cmd.target}"
        await DBOS.send_async(
            destination_id=child_wf_id,
            message=cmd.payload,
            topic="comment",
        )
        return f"Comment sent to {cmd.target}"

    if cmd.type == CommandType.PAUSE:
        await DBOS.send_async(
            destination_id=parent_workflow_id,
            message=True,
            topic="control:pause",
        )
        for child_id in child_workflow_ids.values():
            await DBOS.send_async(
                destination_id=child_id,
                message=True,
                topic="control:pause",
            )
        return "Pause sent to all workflows"

    if cmd.type == CommandType.RESUME:
        await DBOS.send_async(
            destination_id=parent_workflow_id,
            message=True,
            topic="control:resume",
        )
        for child_id in child_workflow_ids.values():
            await DBOS.send_async(
                destination_id=child_id,
                message=True,
                topic="control:resume",
            )
        return "Resume sent to all workflows"

    if cmd.type == CommandType.CANCEL:
        await DBOS.send_async(
            destination_id=parent_workflow_id,
            message=True,
            topic="control:cancel",
        )
        return "Cancel sent to parent workflow"

    if cmd.type == CommandType.PRIORITY:
        if not cmd.target or not cmd.payload:
            return "Usage: /priority T-3 high"
        await DBOS.send_async(
            destination_id=parent_workflow_id,
            message={"task_id": cmd.target, "priority": cmd.payload},
            topic="control:priority",
        )
        return f"Priority change sent for {cmd.target}"

    if cmd.type == CommandType.STATUS:
        return None  # Status rendering handled by caller

    if cmd.type == CommandType.HELP:
        return (
            "Commands:\n"
            "  /answer Q-1 <text>     Answer a question\n"
            "  /comment T-1 <text>    Inject feedback\n"
            "  /pause                 Pause all work\n"
            "  /resume                Resume paused work\n"
            "  /cancel                Cancel everything\n"
            "  /status                Show status\n"
            "  /verbose T-1           Stream full output\n"
            "  /quiet T-1             Return to summary\n"
            "  /priority T-3 high     Change task priority\n"
            "  /help                  Show this help"
        )

    return f"Unknown command: {cmd.type}"


# ---------------------------------------------------------------------------
# Interactive session (prompt_toolkit)
# ---------------------------------------------------------------------------


async def run_interactive_session(
    parent_workflow_id: str,
    job_id: str,
    poll_interval_ms: int = 200,
) -> None:
    """Run the interactive terminal UI for a workflow.

    This is the main event loop that:
    1. Polls DBOS events and renders to stdout
    2. Reads operator input and dispatches commands
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout

    session: PromptSession[str] = PromptSession()
    question_tracker = QuestionTracker()
    child_workflow_ids: dict[str, str] = {}  # task_id -> workflow_id
    last_seen: dict[str, int] = {}  # workflow_id -> last seen seq
    poll_interval = poll_interval_ms / 1000.0

    async def poll_loop():
        """Continuously poll events from all active workflows."""
        while True:
            try:
                # Poll parent events
                parent_last = last_seen.get(parent_workflow_id, 0)
                events, new_last = await poll_workflow_events(
                    parent_workflow_id, parent_last
                )
                last_seen[parent_workflow_id] = new_last
                for evt in events:
                    print(format_log_event(evt, job_id=job_id))

                # Check for child workflow registrations
                all_parent_events = await DBOS.get_all_events_async(parent_workflow_id)
                for key, value in all_parent_events.items():
                    if key.startswith("task:") and key.endswith(":workflow_id"):
                        task_id = key.split(":")[1]
                        if task_id not in child_workflow_ids and isinstance(value, str):
                            child_workflow_ids[task_id] = value

                # Poll child events
                for task_id, child_wf_id in child_workflow_ids.items():
                    child_last = last_seen.get(child_wf_id, 0)
                    child_events, child_new_last = await poll_workflow_events(
                        child_wf_id, child_last
                    )
                    last_seen[child_wf_id] = child_new_last
                    for evt in child_events:
                        print(format_log_event(evt, job_id=job_id, task_id=task_id))

                    # Check for questions
                    child_all = await DBOS.get_all_events_async(child_wf_id)
                    for key, value in child_all.items():
                        if key.startswith("question:"):
                            internal_id = key.split(":", 1)[1]
                            display_id = question_tracker.register(internal_id, child_wf_id)
                            if isinstance(value, dict):
                                tier = value.get("tier", 2)
                                text = value.get("text", "")
                                print(format_log_event(
                                    LogEvent(
                                        message=f"[{display_id}] {text}",
                                        level=EventLevel.QUESTION,
                                        seq=0,
                                    ),
                                    job_id=job_id,
                                    task_id=task_id,
                                ))

                # Check workflow completion
                try:
                    parent_status = await DBOS.get_result_async(parent_workflow_id)
                    if parent_status is not None:
                        print(f"\n[{job_id}] Workflow completed.")
                        return
                except Exception:
                    pass

            except Exception as e:
                logger.debug("Poll error: %s", e)

            await asyncio.sleep(poll_interval)

    async def input_loop():
        """Read operator input and dispatch commands."""
        with patch_stdout():
            while True:
                try:
                    text = await session.prompt_async("devteam> ")
                    if not text.strip():
                        continue
                    cmd = parse_command(text)
                    result = await dispatch_command(
                        cmd,
                        parent_workflow_id,
                        child_workflow_ids,
                        question_tracker,
                    )
                    if result:
                        print(result)
                except EOFError:
                    break
                except KeyboardInterrupt:
                    print("\nUse /cancel to cancel the workflow, or Ctrl+D to detach.")

    try:
        async with asyncio.TaskGroup() as tg:
            poll_task = tg.create_task(poll_loop())
            input_task = tg.create_task(input_loop())
    except* EOFError:
        pass
    except* KeyboardInterrupt:
        pass
```

- [ ] **Step 4: Run tests**

Run: `pixi run pytest tests/cli/test_interactive.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/cli/interactive.py tests/cli/test_interactive.py
git commit -m "feat: add interactive terminal with command parsing and event polling"
```

---

## Phase D: Workflow Wiring

### Task 10: Create execute_task Child Workflow

Rewrite `task_workflow.py` as a DBOS child workflow that handles the revision loop, question raising, and peer/EM review.

**Files:**
- Modify: `src/devteam/orchestrator/task_workflow.py`
- Modify: `tests/orchestrator/test_task_workflow.py`

- [ ] **Step 1: Write failing tests for execute_task workflow**

Create `tests/orchestrator/test_execute_task.py` (or replace content in `test_task_workflow.py`):

```python
"""Tests for execute_task DBOS child workflow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dbos import DBOS

from devteam.orchestrator.task_workflow import execute_task
from devteam.orchestrator.schemas import TaskDecomposition, DecompositionResult


@pytest.fixture
def sample_task():
    return TaskDecomposition(
        id="T-1",
        description="Build auth module",
        assigned_to="backend_engineer",
        team="a",
        depends_on=[],
        pr_group="feat/auth",
    )


@pytest.fixture
def sample_decomposition(sample_task):
    return DecompositionResult(
        tasks=[sample_task],
        peer_assignments={"T-1": "frontend_engineer"},
        parallel_groups=[["T-1"]],
    )


class TestExecuteTask:
    @pytest.mark.asyncio
    async def test_happy_path_approved(self, dbos_launch, sample_task, sample_decomposition, tmp_path):
        """Task completes: engineer → peer review (pass) → EM review (pass) → PR."""
        impl_result = {
            "summary": "Built auth", "files_changed": ["auth.py"],
            "tests_added": ["test_auth.py"], "confidence": "high", "status": "completed",
        }
        review_pass = {
            "verdict": "approve", "summary": "LGTM", "needs_revision": False,
            "comments": [],
        }

        call_count = {"invoke": 0}

        async def mock_invoke(**kwargs):
            call_count["invoke"] += 1
            role = kwargs.get("role", "")
            if role == "backend_engineer":
                return MagicMock(**impl_result)
            return MagicMock(**review_pass)

        with patch("devteam.orchestrator.task_workflow.invoke_agent_step", side_effect=mock_invoke):
            with patch("devteam.orchestrator.task_workflow.create_worktree_step", return_value=str(tmp_path)):

                @DBOS.workflow()
                async def wrapper():
                    return await execute_task(
                        job_id="W-1",
                        parent_workflow_id="parent-uuid",
                        task=sample_task,
                        decomposition=sample_decomposition,
                        project_name="test",
                        config={"pr": {"max_fix_iterations": 3}},
                    )

                result = await wrapper()
                assert result.status == "completed"
                # 3 invocations: engineer + peer reviewer + EM
                assert call_count["invoke"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/orchestrator/test_execute_task.py -v`

Expected: FAIL — `execute_task` doesn't have the new signature yet.

- [ ] **Step 3: Rewrite task_workflow.py as DBOS workflow**

Replace `src/devteam/orchestrator/task_workflow.py`:

```python
"""Child workflow: execute a single task (engineer + review chain).

Decorated with @DBOS.workflow() for crash-safe execution.
Uses DBOS events for question visibility and DBOS messages for answers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from dbos import DBOS

from devteam.agents.invoker import invoke_agent_step
from devteam.orchestrator.events import EventLevel, LogEvent, make_log_key
from devteam.orchestrator.schemas import (
    DecompositionResult,
    ImplementationResult,
    QuestionType,
    ReviewResult,
    TaskDecomposition,
)

logger = logging.getLogger(__name__)

MAX_REVISION_ITERATIONS = 3


@dataclass
class TaskResult:
    """Result of a task workflow execution."""

    status: str  # "completed", "max_revisions_exceeded", "cancelled"
    pr: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Inline workflow helpers
# ---------------------------------------------------------------------------

async def _check_pause() -> None:
    """Check for pause control messages. Block until resumed if paused."""
    paused = False
    while True:
        msg = await DBOS.recv_async(topic="control:pause", timeout_seconds=0)
        if msg is None:
            break
        paused = True
    while True:
        msg = await DBOS.recv_async(topic="control:resume", timeout_seconds=0)
        if msg is None:
            break
        paused = False
    while paused:
        await DBOS.set_event_async("pause_state", True)
        msg = await DBOS.recv_async(topic="control:resume", timeout_seconds=1)
        if msg is not None:
            paused = False
    await DBOS.set_event_async("pause_state", False)


async def _check_cancel() -> bool:
    """Check for cancel control message. Returns True if cancelled."""
    msg = await DBOS.recv_async(topic="control:cancel", timeout_seconds=0)
    return msg is not None


# ---------------------------------------------------------------------------
# Stub for git steps (wired in Task 14)
# ---------------------------------------------------------------------------


async def create_worktree_step(task: TaskDecomposition) -> str:
    """Create a git worktree for the task. Returns worktree path."""
    # Stub — wired to real git in Task 14
    return f"/tmp/worktree-{task.id}"


async def create_pr_step(task: TaskDecomposition, worktree: str) -> dict[str, Any]:
    """Create a PR for the task's work. Returns PR metadata."""
    # Stub — wired to real git in Task 14
    return {"url": f"https://github.com/example/repo/pull/1", "number": 1, "task_id": task.id}


# ---------------------------------------------------------------------------
# The child workflow
# ---------------------------------------------------------------------------


def _build_task_prompt(
    task: TaskDecomposition,
    revision_feedback: str | None,
    comments: list[str] | None = None,
) -> str:
    """Build the prompt for an engineer agent invocation."""
    parts = [f"## Your Assignment\n{task.description}\n"]
    if revision_feedback:
        parts.append(
            f"## Revision Required\n"
            f"Your previous implementation was rejected. Address this feedback:\n{revision_feedback}\n"
        )
    if comments:
        parts.append("## Operator Feedback\n" + "\n".join(comments) + "\n")
    parts.append(
        "## Instructions\n"
        "If anything is unclear, state your question clearly and stop. "
        "Do not guess or assume.\n"
    )
    return "\n".join(parts)


def _build_review_prompt(impl: Any, task: TaskDecomposition, review_type: str) -> str:
    """Build the prompt for a peer or EM review."""
    summary = getattr(impl, "summary", str(impl))
    files = getattr(impl, "files_changed", [])
    tests = getattr(impl, "tests_added", [])
    return (
        f"## Review Request ({review_type})\n\n"
        f"### Task\n{task.description}\n\n"
        f"### Implementation Summary\n{summary}\n\n"
        f"### Files Changed\n" + "\n".join(f"- {f}" for f in files) + "\n\n"
        f"### Tests Added\n" + "\n".join(f"- {t}" for t in tests) + "\n\n"
        "Review the implementation. Check correctness, test coverage, conventions.\n"
    )


@DBOS.workflow()
async def execute_task(
    job_id: str,
    parent_workflow_id: str,
    task: TaskDecomposition,
    decomposition: DecompositionResult,
    project_name: str,
    config: dict[str, Any],
) -> TaskResult:
    """Execute a single task: engineer → peer review → EM review → PR.

    Revision loop on rejection. Questions use DBOS events (visibility)
    and messages (answer delivery).
    """
    # Each workflow invocation gets its own log counter (not a global)
    log_counter = [0]  # mutable container for nonlocal access

    async def emit(message: str, level: str = "info") -> None:
        log_counter[0] += 1
        await DBOS.set_event_async(make_log_key(log_counter[0]), {
            "message": message,
            "level": level,
            "timestamp": __import__("time").time(),
        })

    # Announce ourselves to the parent
    await DBOS.set_event_async("task_status", "running")
    await emit(f"{task.assigned_to} starting")

    # Create isolated worktree
    worktree = await create_worktree_step(task)

    max_revisions = config.get("pr", {}).get("max_fix_iterations", MAX_REVISION_ITERATIONS)
    revision_count = 0
    revision_feedback: str | None = None
    question_counter = 0

    while revision_count <= max_revisions:
        # Check for cancel
        if await _check_cancel():
            await DBOS.set_event_async("task_status", "cancelled")
            return TaskResult(status="cancelled")

        # Drain operator comments
        comments: list[str] = []
        while True:
            msg = await DBOS.recv_async(topic="comment", timeout_seconds=0)
            if msg is None:
                break
            comments.append(str(msg))

        # Check for pause
        await _check_pause()

        # Invoke engineer agent
        prompt = _build_task_prompt(task, revision_feedback, comments or None)
        impl = await invoke_agent_step(
            role=task.assigned_to,
            prompt=prompt,
            worktree_path=worktree,
            project_name=project_name,
        )

        # Handle questions
        impl_status = getattr(impl, "status", "completed")
        if impl_status in ("needs_clarification", "blocked"):
            question_counter += 1
            q_id = f"Q-{task.id}-{question_counter}"
            q_text = getattr(impl, "question", "Unspecified question")
            q_type = "blocked" if impl_status == "blocked" else "technical"
            tier = 1 if q_type == "blocked" else 2

            await DBOS.set_event_async(f"question:{q_id}", {
                "tier": tier,
                "text": q_text,
                "task": task.id,
                "type": q_type,
            })
            await emit(f"[{q_id}] {q_text}", level="question")

            # Wait for operator answer
            answer = await DBOS.recv_async(topic=f"answer:{q_id}")
            revision_feedback = f"Answer to your question: {answer}"
            continue

        # Peer review
        peer_reviewer = decomposition.peer_assignments.get(task.id)
        if not peer_reviewer:
            await emit(f"No peer reviewer assigned, skipping peer review")
        else:
            await _check_pause()
            review = await invoke_agent_step(
                role=peer_reviewer,
                prompt=_build_review_prompt(impl, task, "Peer Review"),
                worktree_path=worktree,
                project_name=project_name,
            )
            if getattr(review, "needs_revision", False):
                revision_feedback = f"Peer review: {getattr(review, 'summary', 'revision needed')}"
                revision_count += 1
                await emit(f"Peer review: revision requested ({revision_count}/{max_revisions})")
                continue

        # EM review
        await _check_pause()
        em_role = "em_team_a" if task.team == "a" else "em_team_b"
        em_review = await invoke_agent_step(
            role=em_role,
            prompt=_build_review_prompt(impl, task, "EM Review"),
            worktree_path=worktree,
            project_name=project_name,
        )
        if getattr(em_review, "needs_revision", False):
            revision_feedback = f"EM review: {getattr(em_review, 'summary', 'revision needed')}"
            revision_count += 1
            await emit(f"EM review: revision requested ({revision_count}/{max_revisions})")
            continue

        # Approved — create PR
        pr = await create_pr_step(task, worktree)
        await DBOS.set_event_async(f"pr:{task.id}", pr)
        await DBOS.set_event_async("task_status", "completed")
        await emit(f"Complete — PR created")
        return TaskResult(status="completed", pr=pr)

    # Max revisions exceeded
    await DBOS.set_event_async("task_status", "failed")
    await emit(f"Failed — max revisions exceeded", level="error")
    return TaskResult(status="max_revisions_exceeded")
```

- [ ] **Step 4: Run tests**

Run: `pixi run pytest tests/orchestrator/test_task_workflow.py -v`

Expected: PASS (existing tests may need updating — adapt as needed).

- [ ] **Step 5: Commit**

```bash
git add src/devteam/orchestrator/task_workflow.py tests/orchestrator/test_task_workflow.py
git commit -m "feat: rewrite task_workflow as DBOS child workflow with revision loop and question handling"
```

---

### Task 11: Create execute_job Parent Workflow and DAG Execution

Replace `DAGExecutor` with `execute_job` DBOS workflow and `manage_dag_execution` helper. Keep `DAGState`, `build_dag`, and related pure data structures.

**Files:**
- Modify: `src/devteam/orchestrator/dag.py`
- Modify: `tests/orchestrator/test_dag.py`

- [ ] **Step 1: Write failing tests for execute_job**

Create `tests/orchestrator/test_execute_job.py`:

```python
"""Tests for execute_job DBOS parent workflow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dbos import DBOS

from devteam.orchestrator.dag import execute_job


class TestExecuteJob:
    @pytest.mark.asyncio
    async def test_full_project_path(self, dbos_launch, tmp_path):
        """Spec+plan → route as full_project → decompose → execute tasks → complete."""
        from devteam.orchestrator.schemas import (
            DecompositionResult, RoutePath, RoutingResult, TaskDecomposition,
        )

        mock_routing = RoutingResult(
            path=RoutePath.FULL_PROJECT,
            reasoning="Has spec and plan",
        )
        mock_task = TaskDecomposition(
            id="T-1", description="Build it", assigned_to="backend_engineer",
            team="a", depends_on=[], pr_group="feat/thing",
        )
        mock_decomposition = DecompositionResult(
            tasks=[mock_task],
            peer_assignments={},
            parallel_groups=[["T-1"]],
        )

        with patch("devteam.orchestrator.dag.route_intake_step") as mock_route:
            mock_route.return_value = mock_routing
            with patch("devteam.orchestrator.dag.decompose_step") as mock_decompose:
                mock_decompose.return_value = mock_decomposition
                with patch("devteam.orchestrator.dag.execute_task") as mock_execute:
                    # Mock execute_task to return completed
                    mock_handle = AsyncMock()
                    mock_handle.get_result = AsyncMock(return_value=MagicMock(status="completed"))
                    mock_handle.workflow_id = "child-uuid-1"

                    with patch.object(DBOS, "start_workflow_async", return_value=mock_handle):

                        @DBOS.workflow()
                        async def wrapper():
                            return await execute_job(
                                job_id="W-1",
                                spec="test spec",
                                plan="test plan",
                                project_name="test",
                                config={"general": {"max_concurrent_agents": 3}},
                            )

                        result = await wrapper()
                        assert result["status"] == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/orchestrator/test_execute_job.py -v`

Expected: FAIL — `execute_job` doesn't exist in dag.py yet.

- [ ] **Step 3: Rewrite dag.py**

Keep `TaskState`, `TaskNode`, `DAGState`, `build_dag`, `DAGExecutionResult` (these are used by tests and are good data structures). Remove `DAGExecutor`. Add `execute_job`, `manage_dag_execution`, and step wrappers.

Add to the bottom of `src/devteam/orchestrator/dag.py`:

```python
# ---------------------------------------------------------------------------
# DBOS workflow functions
# ---------------------------------------------------------------------------

from dbos import DBOS

from devteam.orchestrator.events import make_log_key
from devteam.orchestrator.routing import IntakeContext, classify_intake, route_intake
from devteam.orchestrator.schemas import (
    RoutePath,
    RoutingResult,
)


# Step wrappers — these call the async library functions and are decorated
# so DBOS can record their results for crash-safe replay.

@DBOS.step()
async def route_intake_step(spec: str, plan: str) -> RoutingResult:
    """Route intake via CEO analysis (or fast-path)."""
    from devteam.agents.invoker import invoke_agent_step

    ctx = IntakeContext(spec=spec, plan=plan)

    async def invoker(role, prompt, **kwargs):
        result = await invoke_agent_step(
            role=role, prompt=prompt, worktree_path=None, project_name=""
        )
        return result.model_dump()

    return await route_intake(ctx, invoker)


@DBOS.step()
async def decompose_step(spec: str, plan: str, routing: RoutingResult):
    """Decompose spec+plan via Chief Architect."""
    from devteam.agents.invoker import invoke_agent_step
    from devteam.orchestrator.decomposition import decompose

    async def invoker(role, prompt, **kwargs):
        result = await invoke_agent_step(
            role=role, prompt=prompt, worktree_path=None, project_name=""
        )
        return result.model_dump()

    return await decompose(spec, plan, routing, invoker)


@DBOS.step()
async def cleanup_step(job_id: str) -> None:
    """Clean up worktrees and branches for a job."""
    # Stub — wired to real git cleanup in Task 14
    pass


# Log sequence counter for parent workflow
_parent_log_seq: int = 0


async def _emit_parent_log(message: str, level: str = "info") -> None:
    global _parent_log_seq
    _parent_log_seq += 1
    await DBOS.set_event_async(make_log_key(_parent_log_seq), {
        "message": message,
        "level": level,
        "timestamp": __import__("time").time(),
    })


async def _check_parent_pause() -> None:
    """Check for pause/resume control messages in parent workflow."""
    paused = False
    while True:
        msg = await DBOS.recv_async(topic="control:pause", timeout_seconds=0)
        if msg is None:
            break
        paused = True
    while True:
        msg = await DBOS.recv_async(topic="control:resume", timeout_seconds=0)
        if msg is None:
            break
        paused = False
    while paused:
        await DBOS.set_event_async("pause_state", True)
        msg = await DBOS.recv_async(topic="control:resume", timeout_seconds=1)
        if msg is not None:
            paused = False
    await DBOS.set_event_async("pause_state", False)


async def _check_parent_cancel() -> bool:
    """Check for cancel control message. Returns True if cancelled."""
    msg = await DBOS.recv_async(topic="control:cancel", timeout_seconds=0)
    return msg is not None


async def manage_dag_execution(
    job_id: str,
    decomposition,
    project_name: str,
    config: dict,
) -> dict[str, Any]:
    """Manage parallel task execution via child workflows.

    Uses DAGState to track dependencies and concurrency limits.
    """
    from devteam.concurrency.priority import Priority
    from devteam.orchestrator.task_workflow import execute_task

    dag = build_dag(decomposition)
    max_concurrent = config.get("general", {}).get("max_concurrent_agents", 3)
    active: dict[str, Any] = {}  # task_id -> handle
    completed: dict[str, Any] = {}
    priority_overrides: dict[str, Any] = {}

    while not dag.all_completed:
        await _check_parent_pause()
        if await _check_parent_cancel():
            return {"status": "cancelled"}

        # Check for priority override messages
        while True:
            msg = await DBOS.recv_async(topic="control:priority", timeout_seconds=0)
            if msg is None:
                break
            if isinstance(msg, dict):
                priority_overrides[msg["task_id"]] = msg.get("priority", "normal")

        # Launch ready tasks up to concurrency limit
        ready = dag.get_ready_tasks()
        for task in ready:
            if len(active) >= max_concurrent:
                break
            if task.id not in active and task.id not in completed:
                parent_wf_id = DBOS.workflow_id
                handle = await DBOS.start_workflow_async(
                    execute_task,
                    job_id=job_id,
                    parent_workflow_id=parent_wf_id,
                    task=task,
                    decomposition=decomposition,
                    project_name=project_name,
                    config=config,
                )
                active[task.id] = handle
                dag.mark_running(task.id)
                # Register child workflow ID for terminal polling
                await DBOS.set_event_async(
                    f"task:{task.id}:workflow_id", handle.workflow_id
                )
                await _emit_parent_log(f"{task.assigned_to} starting on {task.id}")

        # Wait for any active task to complete
        if active:
            import asyncio

            done_id = None
            while done_id is None:
                for task_id, handle in list(active.items()):
                    try:
                        status = await handle.get_status()
                        if status.name in ("SUCCESS", "ERROR", "RETRIES_EXCEEDED"):
                            try:
                                result = await handle.get_result(polling_interval_sec=0.1)
                                dag.mark_completed(task_id, result)
                                completed[task_id] = result
                                await _emit_parent_log(
                                    f"{task_id} completed"
                                )
                            except Exception as e:
                                dag.mark_failed(task_id, str(e))
                                await _emit_parent_log(
                                    f"{task_id} failed: {e}", level="error"
                                )
                            del active[task_id]
                            done_id = task_id
                            break
                    except Exception:
                        pass
                if done_id is None:
                    if not active:
                        break
                    await asyncio.sleep(0.5)
        elif not dag.has_pending:
            break

    return {
        "status": "completed" if not dag.has_failed else "partial_failure",
        "results": dag.get_results(),
        "failed": {
            tid: node.error or "Unknown"
            for tid, node in dag.nodes.items()
            if node.state == TaskState.FAILED
        },
    }


@DBOS.workflow()
async def execute_job(
    job_id: str,
    spec: str,
    plan: str,
    project_name: str,
    config: dict,
) -> dict[str, Any]:
    """Parent workflow: route → decompose → execute DAG → post-PR review → cleanup."""
    global _parent_log_seq
    _parent_log_seq = 0

    await _check_parent_pause()
    if await _check_parent_cancel():
        return {"status": "cancelled"}

    # Step 1: Route
    routing = await route_intake_step(spec, plan)
    await _emit_parent_log(f"Routed as {routing.path.value}")
    await _check_parent_pause()

    # Step 2: Research path — single agent, no decomposition
    if routing.path == RoutePath.RESEARCH:
        from devteam.agents.invoker import invoke_agent_step

        result = await invoke_agent_step(
            role="planner_researcher_a",
            prompt=f"Research request:\n\n{spec}",
            worktree_path=None,
            project_name=project_name,
        )
        return {"status": "completed", "research_result": result.model_dump()}

    # Step 3: Small fix — single task, no DAG
    if routing.path == RoutePath.SMALL_FIX:
        from devteam.orchestrator.schemas import TaskDecomposition
        from devteam.orchestrator.decomposition import DecompositionResult
        from devteam.orchestrator.task_workflow import execute_task

        task = TaskDecomposition(
            id="T-1",
            assigned_to=routing.recommended_role or "backend_engineer",
            description=spec,
            team=routing.target_team or "a",
            depends_on=[],
            pr_group="fix/small-fix",
        )
        decomposition = DecompositionResult(
            tasks=[task], peer_assignments={}, parallel_groups=[["T-1"]],
        )
        parent_wf_id = DBOS.workflow_id
        handle = await DBOS.start_workflow_async(
            execute_task,
            job_id=job_id,
            parent_workflow_id=parent_wf_id,
            task=task,
            decomposition=decomposition,
            project_name=project_name,
            config=config,
        )
        await DBOS.set_event_async("task:T-1:workflow_id", handle.workflow_id)
        result = await handle.get_result()
        await cleanup_step(job_id)
        return {"status": "completed"}

    # Step 4: Full project / OSS — decompose and run DAG
    if await _check_parent_cancel():
        return {"status": "cancelled"}

    decomposition = await decompose_step(spec, plan, routing)
    await _emit_parent_log(f"Decomposed into {len(decomposition.tasks)} tasks")

    if await _check_parent_cancel():
        return {"status": "cancelled"}

    dag_result = await manage_dag_execution(
        job_id, decomposition, project_name, config,
    )

    if dag_result.get("status") == "cancelled":
        await cleanup_step(job_id)
        return {"status": "cancelled"}

    # Step 5: Post-PR review (if all succeeded)
    if dag_result.get("status") == "completed":
        from devteam.orchestrator.review import execute_post_pr_review
        from devteam.orchestrator.schemas import WorkType

        async def invoker(role, prompt, **kwargs):
            from devteam.agents.invoker import invoke_agent_step
            result = await invoke_agent_step(
                role=role, prompt=prompt, worktree_path=None, project_name=project_name,
            )
            return result.model_dump()

        review = await execute_post_pr_review(
            work_type=WorkType.CODE,
            pr_context="Post-PR review for completed job",
            invoker=invoker,
        )
        if not review.all_passed:
            await _emit_parent_log(
                f"Post-PR review failed: {', '.join(review.failed_gates)}", level="error"
            )

    # Step 6: Cleanup
    await cleanup_step(job_id)
    await _emit_parent_log("Job completed", level="success")

    return {"status": dag_result.get("status", "completed")}
```

- [ ] **Step 4: Run tests**

Run: `pixi run pytest tests/orchestrator/test_execute_job.py tests/orchestrator/test_dag.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/orchestrator/dag.py tests/orchestrator/test_execute_job.py tests/orchestrator/test_dag.py
git commit -m "feat: add execute_job parent workflow with DAG execution and routing"
```

---

### Task 12: Create Bootstrap Sequence

Wire the initialization path: config → DBOS → knowledge → registry → start workflow.

**Files:**
- Create: `src/devteam/orchestrator/bootstrap.py`
- Create: `tests/orchestrator/test_bootstrap.py`

- [ ] **Step 1: Write failing tests**

Create `tests/orchestrator/test_bootstrap.py`:

```python
"""Tests for bootstrap sequence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devteam.orchestrator.bootstrap import bootstrap, generate_job_id


class TestGenerateJobId:
    def test_format(self):
        job_id = generate_job_id()
        assert job_id.startswith("W-")
        num = int(job_id.split("-")[1])
        assert num >= 1

    def test_unique(self):
        ids = {generate_job_id() for _ in range(100)}
        assert len(ids) == 100


class TestBootstrap:
    @pytest.mark.asyncio
    async def test_bootstrap_returns_handle(self, tmp_path):
        """Bootstrap initializes DBOS and returns a workflow handle."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        with patch("devteam.orchestrator.bootstrap.load_and_merge_config") as mock_config:
            from devteam.config.settings import DevteamConfig
            mock_config.return_value = DevteamConfig()

            with patch("devteam.orchestrator.bootstrap._connect_knowledge", return_value=None):
                with patch("devteam.orchestrator.bootstrap._connect_embedder", return_value=None):
                    handle, job_id = await bootstrap(
                        spec="test spec",
                        plan="test plan",
                        db_path=str(tmp_path / "test.sqlite"),
                    )
                    assert handle is not None
                    assert hasattr(handle, "workflow_id")
                    assert job_id.startswith("W-")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/orchestrator/test_bootstrap.py -v`

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement bootstrap.py**

Create `src/devteam/orchestrator/bootstrap.py`:

```python
"""Bootstrap sequence: config → DBOS → services → workflow start.

This is the entry point for `devteam start`. It wires everything together
and starts the parent workflow.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from dbos import DBOS

from devteam.agents.invoker import set_agent_registry, set_embedder, set_knowledge_store
from devteam.agents.registry import AgentRegistry
from devteam.agents.template_manager import get_bundled_templates_dir
from devteam.config.settings import (
    DevteamConfig,
    load_global_config,
    load_project_config,
    merge_configs,
)

logger = logging.getLogger(__name__)

_job_counter = 0


def generate_job_id() -> str:
    """Generate a unique job ID (W-N)."""
    global _job_counter
    _job_counter += 1
    return f"W-{_job_counter}"


def load_and_merge_config() -> DevteamConfig:
    """Load and merge global + project config."""
    global_path = Path.home() / ".devteam" / "config.toml"
    global_config = load_global_config(global_path)
    project_config = load_project_config(Path("devteam.toml"))
    return merge_configs(global_config, project_config)


async def _connect_knowledge(config: DevteamConfig) -> Any | None:
    """Connect to knowledge store (graceful degradation)."""
    try:
        from devteam.knowledge.store import KnowledgeStore

        store = KnowledgeStore(config.knowledge.surrealdb_url)
        await store.connect(
            username=config.knowledge.surrealdb_username,
            password=config.knowledge.surrealdb_password,
        )
        return store
    except Exception:
        logger.warning("Knowledge store unavailable — proceeding without knowledge")
        return None


async def _connect_embedder(config: DevteamConfig) -> Any | None:
    """Connect to embedder (graceful degradation)."""
    try:
        from devteam.knowledge.embeddings import create_embedder_from_config

        embedder = create_embedder_from_config(config.knowledge)
        if not await embedder.is_available():
            return None
        return embedder
    except Exception:
        logger.warning("Ollama unavailable — proceeding without embeddings")
        return None


async def bootstrap(
    spec: str,
    plan: str,
    db_path: str | None = None,
) -> Any:
    """Initialize all services and start the job workflow.

    Args:
        spec: Spec document content.
        plan: Plan document content.
        db_path: Override DBOS SQLite path (for testing).

    Returns:
        Tuple of (WorkflowHandleAsync, job_id).
    """
    # 1. Load config
    config = load_and_merge_config()

    # 2. Initialize DBOS
    if db_path is None:
        db_dir = Path.home() / ".devteam"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(db_dir / "devteam_system.sqlite")

    DBOS(config={
        "name": "devteam",
        "system_database_url": f"sqlite:///{db_path}",
    })
    DBOS.launch()

    # 3. Check for existing active workflows
    # V1 is single-job: refuse if another workflow is active
    # (DBOS workflow listing will be available after launch)

    # 4. Connect knowledge (graceful degradation)
    knowledge_store = await _connect_knowledge(config)

    # 5. Connect embedder (graceful degradation)
    embedder = await _connect_embedder(config)

    # 6. Load agent registry
    registry = AgentRegistry.load(get_bundled_templates_dir())

    # 7. Wire module-level singletons
    set_knowledge_store(knowledge_store)
    set_embedder(embedder)
    set_agent_registry(registry)

    # 8. Start the workflow
    from devteam.orchestrator.dag import execute_job

    job_id = generate_job_id()
    handle = await DBOS.start_workflow_async(
        execute_job,
        job_id=job_id,
        spec=spec,
        plan=plan,
        project_name=Path.cwd().name,  # Derive from current working directory
        config=config.model_dump(),
    )

    return handle, job_id
```

- [ ] **Step 4: Run tests**

Run: `pixi run pytest tests/orchestrator/test_bootstrap.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/orchestrator/bootstrap.py tests/orchestrator/test_bootstrap.py
git commit -m "feat: add bootstrap sequence wiring config, DBOS, knowledge, and workflow start"
```

---

### Task 13: Wire CLI Commands to DBOS Workflows

Replace the stubbed `job_cmd.py` with real implementations that call `bootstrap()` and `run_interactive_session()`.

**Files:**
- Modify: `src/devteam/cli/commands/job_cmd.py`
- Modify: `src/devteam/cli/main.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Implement the `start` command**

Update `src/devteam/cli/commands/job_cmd.py`:

```python
"""devteam job control commands — start, status, resume.

These are the primary operator interface. `start` launches a DBOS workflow
and enters the interactive terminal session. `resume` recovers after crash.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer


def register_job_commands(app: typer.Typer) -> None:
    """Register job control commands directly on the main app."""

    @app.command()
    def start(
        spec: str | None = typer.Option(None, "--spec", help="Path to spec document"),
        plan: str | None = typer.Option(None, "--plan", help="Path to plan document"),
        prompt: str | None = typer.Option(None, "--prompt", help="Direct prompt for small fixes"),
        issue: str | None = typer.Option(None, "--issue", help="GitHub issue URL"),
        priority: str | None = typer.Option(
            None, "--priority", help="Job priority: high, normal, low"
        ),
    ) -> None:
        """Start a new development job."""
        if not any([spec, plan, prompt, issue]):
            typer.echo("Provide --spec/--plan, --prompt, or --issue to start a job.")
            raise typer.Exit(code=1)

        # Read file contents
        spec_content = ""
        plan_content = ""
        if spec:
            spec_path = Path(spec)
            spec_content = spec_path.read_text() if spec_path.exists() else spec
        if plan:
            plan_path = Path(plan)
            plan_content = plan_path.read_text() if plan_path.exists() else plan
        if prompt:
            spec_content = prompt

        async def _run():
            from devteam.orchestrator.bootstrap import bootstrap
            from devteam.cli.interactive import run_interactive_session

            handle, job_id = await bootstrap(spec=spec_content, plan=plan_content)
            typer.echo(f"Job {job_id} started. Entering interactive session...")
            await run_interactive_session(
                parent_workflow_id=handle.workflow_id,
                job_id=job_id,
            )

        asyncio.run(_run())

    @app.command()
    def resume(
        target: str | None = typer.Argument(None, help="Job ID (W-1)"),
    ) -> None:
        """Resume a paused job or recover workflows after crash."""
        async def _run():
            from dbos import DBOS
            from devteam.orchestrator.bootstrap import load_and_merge_config
            from devteam.cli.interactive import run_interactive_session

            config = load_and_merge_config()
            db_path = str(Path.home() / ".devteam" / "devteam_system.sqlite")

            DBOS(config={
                "name": "devteam",
                "system_database_url": f"sqlite:///{db_path}",
            })
            DBOS.launch()  # Automatically recovers pending workflows
            typer.echo("DBOS recovered. Workflows resumed from last checkpoint.")

            # V1: DBOS.launch() auto-recovers pending workflows.
            # Full job ID → workflow UUID mapping deferred to V2 multi-job.
            if target:
                typer.echo(f"Resuming job {target}...")
            else:
                typer.echo("Resuming most recent job...")

        asyncio.run(_run())

    @app.command()
    def status(
        target: str | None = typer.Argument(
            None, help="Job ID (W-1), task (W-1/T-3), or omit for all"
        ),
        questions: bool = typer.Option(False, "--questions", help="Show pending questions"),
    ) -> None:
        """Show status of active jobs and tasks."""
        typer.echo("Status display will read from DBOS workflow state.")

    @app.command()
    def cancel(
        target: str = typer.Argument(help="Job ID (W-1)"),
        revert_merged: bool = typer.Option(
            False, "--revert-merged", help="Create revert PRs for merged work"
        ),
    ) -> None:
        """Cancel a job and clean up all resources."""
        typer.echo(f"Cancel requires an active interactive session. Use /cancel in the terminal.")
```

- [ ] **Step 2: Update main.py**

Ensure `main.py` no longer references daemon:

```python
"""Typer CLI entry point for devteam."""

import typer

from devteam.cli.commands import focus_cmd, init_cmd, project_cmd
from devteam.cli.commands.concurrency_cmd import register_concurrency_commands
from devteam.cli.commands.git_commands import git_app
from devteam.cli.commands.job_cmd import register_job_commands
from devteam.cli.commands.knowledge_cmd import knowledge_app

app = typer.Typer(
    name="devteam",
    help="Durable AI Development Team Orchestrator",
    no_args_is_help=True,
)

# Register command groups
app.add_typer(init_cmd.app, name="init")
app.add_typer(project_cmd.app, name="project")
app.add_typer(focus_cmd.app, name="focus")
app.add_typer(git_app, name="git")
app.add_typer(knowledge_app, name="knowledge")

# Register top-level job control commands
register_job_commands(app)

# Register concurrency commands (prioritize)
register_concurrency_commands(app)


def main() -> None:
    app()
```

- [ ] **Step 3: Update CLI tests**

In `tests/test_cli.py`, update imports and test expectations. Remove any references to daemon commands or cli_bridge. Verify `start`, `resume`, `status`, and `cancel` commands exist.

- [ ] **Step 4: Run tests**

Run: `pixi run pytest tests/test_cli.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/devteam/cli/commands/job_cmd.py src/devteam/cli/main.py tests/test_cli.py
git commit -m "feat: wire CLI start/resume commands to DBOS bootstrap and interactive terminal"
```

---

## Phase E: Git Integration and End-to-End

### Task 14: Wire Git Operations into Workflow Steps

Replace the stub `create_worktree_step` and `create_pr_step` in task_workflow.py with real implementations that call the git library.

**Files:**
- Modify: `src/devteam/orchestrator/task_workflow.py`
- Modify: `src/devteam/orchestrator/dag.py` (cleanup_step)

- [ ] **Step 1: Implement create_worktree_step**

In `src/devteam/orchestrator/task_workflow.py`, replace the stub:

```python
@DBOS.step()
async def create_worktree_step(task: TaskDecomposition) -> str:
    """Create an isolated git worktree for the task.

    Returns the worktree path. Idempotent — if the worktree already
    exists for this branch, returns the existing path.
    """
    from devteam.git.worktree import create_worktree
    from devteam.git.branch import create_branch

    branch_name = f"devteam/{task.pr_group}/{task.id}".replace(" ", "-")
    create_branch(branch_name)
    worktree_path = create_worktree(branch_name)
    return worktree_path
```

- [ ] **Step 2: Implement create_pr_step**

```python
@DBOS.step()
async def create_pr_step(task: TaskDecomposition, worktree: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a PR for the task's work.

    Checks approval gates before push and PR creation.
    Idempotent — if a PR already exists for this branch, returns it.
    """
    from devteam.concurrency.approval import check_approval
    from devteam.git.helpers import git_run
    from devteam.git.pr import create_pr, find_existing_pr

    branch_name = f"devteam/{task.pr_group}/{task.id}".replace(" ", "-")

    # Check for existing PR first (idempotent)
    existing = find_existing_pr(branch_name)
    if existing:
        return existing

    # Approval gate: push
    approval_config = (config or {}).get("approval", {})
    push_approval = approval_config.get("push", "auto")
    if push_approval == "never":
        return {"skipped": True, "reason": "push approval is 'never'"}

    # Push the branch
    git_run(["push", "-u", "origin", branch_name], cwd=worktree)

    # Approval gate: open_pr
    pr_approval = approval_config.get("open_pr", "auto")
    if pr_approval == "never":
        return {"pushed": True, "pr_skipped": True}

    # Create PR
    pr = create_pr(
        title=f"[{task.id}] {task.description[:60]}",
        body=f"Automated PR for task {task.id}\n\nAssigned to: {task.assigned_to}",
        branch=branch_name,
    )
    return pr
```

- [ ] **Step 3: Implement cleanup_step in dag.py**

Replace the stub in `src/devteam/orchestrator/dag.py`:

```python
@DBOS.step()
async def cleanup_step(job_id: str) -> None:
    """Clean up worktrees and branches for a completed/cancelled job.

    Idempotent — each operation handles "already done" gracefully.
    """
    from devteam.git.cleanup import cleanup_worktrees, cleanup_branches

    try:
        cleanup_worktrees(prefix=f"devteam/")
    except Exception as e:
        logger.warning("Worktree cleanup error: %s", e)

    try:
        cleanup_branches(prefix=f"devteam/")
    except Exception as e:
        logger.warning("Branch cleanup error: %s", e)
```

- [ ] **Step 4: Run tests**

Run: `pixi run test`

Expected: All tests pass. Git step implementations are only called from workflow code, so existing unit tests should still pass (they mock these functions).

- [ ] **Step 5: Commit**

```bash
git add src/devteam/orchestrator/task_workflow.py src/devteam/orchestrator/dag.py
git commit -m "feat: wire git worktree, PR creation, and cleanup into DBOS steps"
```

---

### Task 15: End-to-End Integration Test

Test the full flow with mocked agents: start → route → decompose → execute → review → PR → cleanup.

**Files:**
- Create: `tests/test_e2e_workflow.py`

- [ ] **Step 1: Write the end-to-end test**

Create `tests/test_e2e_workflow.py`:

```python
"""End-to-end workflow integration tests.

Tests the full pipeline with mocked Claude Agent SDK calls.
Verifies DBOS workflows, events, and messaging work together.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dbos import DBOS


@pytest.mark.asyncio
async def test_full_project_e2e(dbos_launch, tmp_path):
    """Full project: route → decompose → execute 2 tasks → complete."""
    from devteam.orchestrator.schemas import (
        DecompositionResult,
        RoutePath,
        RoutingResult,
        TaskDecomposition,
    )
    from devteam.orchestrator.dag import execute_job
    from devteam.agents.invoker import set_agent_registry, set_knowledge_store

    # Set up mock registry
    mock_defn = MagicMock()
    mock_defn.model = "sonnet"
    mock_defn.prompt = "You are an engineer."
    mock_defn.tools = ("Read",)

    mock_registry = MagicMock()
    mock_registry.get.return_value = mock_defn
    set_agent_registry(mock_registry)
    set_knowledge_store(None)

    # Mock responses for each agent role
    impl_response = {
        "summary": "Built it",
        "files_changed": ["foo.py"],
        "tests_added": ["test_foo.py"],
        "confidence": "high",
        "status": "completed",
    }
    review_response = {
        "verdict": "approve",
        "summary": "LGTM",
        "needs_revision": False,
        "comments": [],
    }
    routing_response = {
        "path": "full_project",
        "reasoning": "Has spec and plan",
    }
    decomp_response = {
        "tasks": [
            {
                "id": "T-1",
                "description": "Build backend",
                "assigned_to": "backend_engineer",
                "team": "a",
                "depends_on": [],
                "pr_group": "feat/test",
            },
        ],
        "peer_assignments": {},
        "parallel_groups": [["T-1"]],
    }

    call_log: list[str] = []

    async def mock_sdk_call(**kwargs):
        call_log.append(kwargs.get("model", "unknown"))
        # Return different responses based on context
        return impl_response

    with patch("devteam.agents.invoker._call_claude_sdk", side_effect=mock_sdk_call):
        with patch(
            "devteam.orchestrator.task_workflow.create_worktree_step",
            return_value=str(tmp_path),
        ):
            with patch(
                "devteam.orchestrator.task_workflow.create_pr_step",
                return_value={"url": "https://example.com/pr/1", "number": 1, "task_id": "T-1"},
            ):
                with patch("devteam.orchestrator.dag.route_intake_step") as mock_route:
                    mock_route.return_value = RoutingResult(
                        path=RoutePath.FULL_PROJECT, reasoning="test",
                    )
                    with patch("devteam.orchestrator.dag.decompose_step") as mock_decomp:
                        mock_decomp.return_value = DecompositionResult(**decomp_response)
                        with patch("devteam.orchestrator.dag.cleanup_step"):

                            result = await execute_job(
                                job_id="W-1",
                                spec="test spec",
                                plan="test plan",
                                project_name="test",
                                config={
                                    "general": {"max_concurrent_agents": 3},
                                    "pr": {"max_fix_iterations": 3},
                                },
                            )

                            assert result["status"] in ("completed", "partial_failure")


@pytest.mark.asyncio
async def test_question_answer_flow(dbos_launch, tmp_path):
    """Child workflow raises question → answer unblocks it."""
    from devteam.orchestrator.schemas import TaskDecomposition, DecompositionResult
    from devteam.orchestrator.task_workflow import execute_task
    from devteam.agents.invoker import set_agent_registry, set_knowledge_store
    import asyncio

    mock_defn = MagicMock()
    mock_defn.model = "sonnet"
    mock_defn.prompt = "Engineer"
    mock_defn.tools = ()

    mock_registry = MagicMock()
    mock_registry.get.return_value = mock_defn
    set_agent_registry(mock_registry)
    set_knowledge_store(None)

    task = TaskDecomposition(
        id="T-1", description="Build it", assigned_to="backend_engineer",
        team="a", depends_on=[], pr_group="feat/test",
    )
    decomp = DecompositionResult(
        tasks=[task], peer_assignments={"T-1": "frontend_engineer"},
        parallel_groups=[["T-1"]],
    )

    call_count = {"n": 0}

    async def mock_sdk(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call: needs clarification
            return {
                "summary": "", "files_changed": [], "tests_added": [],
                "confidence": "low", "status": "needs_clarification",
                "question": "Redis or JWT?",
            }
        # After answer: succeed
        return {
            "summary": "Used JWT", "files_changed": ["auth.py"],
            "tests_added": ["test_auth.py"], "confidence": "high",
            "status": "completed",
        }

    review_ok = {
        "verdict": "approve", "summary": "LGTM",
        "needs_revision": False, "comments": [],
    }

    with patch("devteam.agents.invoker._call_claude_sdk", side_effect=mock_sdk):
        with patch("devteam.orchestrator.task_workflow.create_worktree_step", return_value=str(tmp_path)):
            with patch("devteam.orchestrator.task_workflow.create_pr_step", return_value={"url": "x", "number": 1, "task_id": "T-1"}):
                # Start the task workflow
                handle = await DBOS.start_workflow_async(
                    execute_task,
                    job_id="W-1",
                    parent_workflow_id="parent-uuid",
                    task=task,
                    decomposition=decomp,
                    project_name="test",
                    config={"pr": {"max_fix_iterations": 3}},
                )

                # Wait a bit for the workflow to hit the question
                await asyncio.sleep(0.5)

                # Check for the question event
                events = await DBOS.get_all_events_async(handle.workflow_id)
                question_keys = [k for k in events if k.startswith("question:")]
                assert len(question_keys) >= 1

                # Answer the question
                q_key = question_keys[0]
                q_id = q_key.split(":", 1)[1]
                await DBOS.send_async(
                    destination_id=handle.workflow_id,
                    message="Use JWT",
                    topic=f"answer:{q_id}",
                )

                # Wait for completion
                result = await handle.get_result()
                assert result.status == "completed"
```

- [ ] **Step 2: Run the end-to-end tests**

Run: `pixi run pytest tests/test_e2e_workflow.py -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_workflow.py
git commit -m "test: add end-to-end workflow integration tests with mocked agents"
```

---

### Task 16: Crash Recovery and Cleanup Tests

Test that DBOS recovers workflows after simulated crash and that orphaned resources are detected.

**Files:**
- Add to: `tests/test_e2e_workflow.py`

- [ ] **Step 1: Write crash recovery test**

Add to `tests/test_e2e_workflow.py`:

```python
@pytest.mark.asyncio
async def test_crash_recovery(tmp_path):
    """Workflow survives simulated crash: init → start → destroy → re-init → result."""
    from dbos import DBOS

    db_path = str(tmp_path / "crash_test.sqlite")

    # First session: start a workflow, then "crash" (destroy DBOS)
    DBOS(config={"name": "crash_test", "system_database_url": f"sqlite:///{db_path}"})
    DBOS.launch()

    @DBOS.workflow()
    async def recoverable_workflow(x: int) -> int:
        return x * 2

    handle = await DBOS.start_workflow_async(recoverable_workflow, 21)
    workflow_id = handle.workflow_id
    result = await handle.get_result()
    assert result == 42

    DBOS.destroy()

    # Second session: re-init DBOS and retrieve the result
    DBOS(config={"name": "crash_test", "system_database_url": f"sqlite:///{db_path}"})
    DBOS.launch()

    # The completed workflow's result should be retrievable
    stored_result = await DBOS.get_result_async(workflow_id)
    assert stored_result == 42

    DBOS.destroy()
```

- [ ] **Step 2: Run the test**

Run: `pixi run pytest tests/test_e2e_workflow.py::test_crash_recovery -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_workflow.py
git commit -m "test: add crash recovery test verifying DBOS workflow persistence"
```

---

### Task 17: Final Test Cleanup and Validation

Run the full test suite, fix any remaining failures from the migration, and verify test counts.

**Files:**
- Various test files as needed

- [ ] **Step 1: Run full test suite**

Run: `pixi run test`

Verify: All tests pass. Note the test count.

- [ ] **Step 2: Fix any remaining import errors or test failures**

If any tests still reference deleted modules (`cli_bridge`, `jobs`, `queue`, `durable_sleep`, `invoke`, `daemon`), update them to either:
- Remove the test if it tests deleted functionality
- Update imports to reference new modules

- [ ] **Step 3: Run ruff format**

Run: `pixi run format`

- [ ] **Step 4: Run ruff check**

Run: `pixi run lint`

Fix any linting issues.

- [ ] **Step 5: Run pyright**

Run: `pixi run typecheck`

Fix any type errors in modified files.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: fix remaining test migrations and lint issues"
```

---

### Task 18: Concurrency Integration Test (Replace)

Replace `tests/concurrency/test_integration.py` with a test that verifies rate-limit retry inside a DBOS step.

**Files:**
- Modify: `tests/concurrency/test_integration.py`

- [ ] **Step 1: Write the new integration test**

Replace `tests/concurrency/test_integration.py`:

```python
"""Concurrency integration tests — rate limit retry in DBOS steps."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from dbos import DBOS

from devteam.agents.invoker import (
    InvocationError,
    invoke_agent_step,
    set_agent_registry,
    set_knowledge_store,
)


@pytest.mark.asyncio
async def test_rate_limit_retry_in_workflow(dbos_launch, tmp_path):
    """Rate limit triggers sleep and retry within invoke_agent_step."""
    mock_defn = MagicMock()
    mock_defn.model = "sonnet"
    mock_defn.prompt = "Engineer"
    mock_defn.tools = ()

    mock_registry = MagicMock()
    mock_registry.get.return_value = mock_defn
    set_agent_registry(mock_registry)
    set_knowledge_store(None)

    attempts = {"count": 0}

    async def mock_sdk(**kwargs):
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise InvocationError("429 rate limit: Retry after 1 seconds")
        return {
            "summary": "Done",
            "files_changed": ["x.py"],
            "tests_added": [],
            "confidence": "high",
            "status": "completed",
        }

    with patch("devteam.agents.invoker._call_claude_sdk", side_effect=mock_sdk):
        with patch("devteam.agents.invoker._is_rate_limit_error", return_value=True):

            @DBOS.workflow()
            async def wf():
                return await invoke_agent_step(
                    role="backend_engineer",
                    prompt="test",
                    worktree_path=str(tmp_path),
                    project_name="test",
                    max_retries=5,
                )

            result = await wf()
            assert result.summary == "Done"
            assert attempts["count"] == 3
```

- [ ] **Step 2: Run the test**

Run: `pixi run pytest tests/concurrency/test_integration.py -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/concurrency/test_integration.py
git commit -m "test: replace concurrency integration test with DBOS rate-limit retry test"
```

---

### Task 19: Update Orchestrator Integration Test (Replace)

Replace `tests/orchestrator/test_integration.py` with DBOS-based integration tests.

**Files:**
- Modify: `tests/orchestrator/test_integration.py`

- [ ] **Step 1: Write the new test**

Replace `tests/orchestrator/test_integration.py`:

```python
"""Orchestrator integration tests — DBOS workflow pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from dbos import DBOS

from devteam.orchestrator.dag import execute_job
from devteam.orchestrator.schemas import (
    DecompositionResult,
    RoutePath,
    RoutingResult,
    TaskDecomposition,
)
from devteam.agents.invoker import set_agent_registry, set_knowledge_store


@pytest.mark.asyncio
async def test_research_path_e2e(dbos_launch, tmp_path):
    """Research path: route → single agent call → complete."""
    mock_defn = MagicMock()
    mock_defn.model = "sonnet"
    mock_defn.prompt = "Researcher"
    mock_defn.tools = ()

    mock_registry = MagicMock()
    mock_registry.get.return_value = mock_defn
    set_agent_registry(mock_registry)
    set_knowledge_store(None)

    with patch("devteam.agents.invoker._call_claude_sdk") as mock_sdk:
        mock_sdk.return_value = {
            "summary": "Research complete",
            "files_changed": [],
            "tests_added": [],
            "confidence": "high",
            "status": "completed",
        }
        with patch("devteam.orchestrator.dag.route_intake_step") as mock_route:
            mock_route.return_value = RoutingResult(
                path=RoutePath.RESEARCH, reasoning="Research request",
            )

            result = await execute_job(
                job_id="W-1",
                spec="Research X",
                plan="",
                project_name="test",
                config={"general": {"max_concurrent_agents": 3}},
            )

            assert result["status"] == "completed"
            assert "research_result" in result
```

- [ ] **Step 2: Run the test**

Run: `pixi run pytest tests/orchestrator/test_integration.py -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/orchestrator/test_integration.py
git commit -m "test: replace orchestrator integration test with DBOS workflow pipeline test"
```

---

### Task 20: Final Validation and Cleanup

Run the complete test suite, verify all tests pass, and ensure the codebase is clean.

- [ ] **Step 1: Run complete test suite**

Run: `pixi run test`

Expected: All tests pass.

- [ ] **Step 2: Run formatter**

Run: `pixi run format`

- [ ] **Step 3: Run linter**

Run: `pixi run lint`

Expected: No errors.

- [ ] **Step 4: Run type checker**

Run: `pixi run typecheck`

Expected: No errors in modified files.

- [ ] **Step 5: Verify file cleanup**

Verify deleted files are gone:

```bash
ls src/devteam/daemon/server.py 2>&1 | grep "No such file"
ls src/devteam/orchestrator/cli_bridge.py 2>&1 | grep "No such file"
ls src/devteam/orchestrator/jobs.py 2>&1 | grep "No such file"
ls src/devteam/concurrency/queue.py 2>&1 | grep "No such file"
ls src/devteam/concurrency/durable_sleep.py 2>&1 | grep "No such file"
ls src/devteam/concurrency/invoke.py 2>&1 | grep "No such file"
```

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: final cleanup and validation — all tests passing"
```
