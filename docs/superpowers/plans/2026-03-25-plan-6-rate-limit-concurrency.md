# Plan 6: Rate Limit & Concurrency Implementation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build the concurrency controls, rate limit handling, and priority system that manage API capacity across all jobs.

**Architecture:** All agent invocations flow through a single DBOS Queue with configurable concurrency. A global pause flag in SQLite coordinates rate limit backoff across all workflows. Priority levels (high/normal/low) control dequeue order, and configurable approval gates allow operator control over side-effecting actions (commit, push, open_pr, merge, cleanup).

**Tech Stack:** DBOS (Python SDK) for durable queues and workflows, SQLite for pause flag and approval config, Click/Typer for CLI commands, pytest + unittest.mock for testing

---

## Project Structure

```
src/
  devteam/
    concurrency/
      __init__.py
      queue.py            # DBOS queue setup, enqueue logic
      rate_limit.py       # rate limit detection, global pause flag, durable sleep
      priority.py         # priority levels, task prioritization
      approval.py         # configurable approval gates
```

---

## Task 1: Priority Levels Module

**File:** `src/devteam/concurrency/priority.py`

### Steps

- [ ] **1a. Write failing tests for priority enum and comparison**

  **File:** `tests/concurrency/test_priority.py`

  ```python
  """Tests for priority levels and ordering."""
  import pytest
  from devteam.concurrency.priority import Priority, prioritize_tasks


  class TestPriority:
      def test_high_greater_than_normal(self):
          assert Priority.HIGH > Priority.NORMAL

      def test_normal_greater_than_low(self):
          assert Priority.NORMAL > Priority.LOW

      def test_high_greater_than_low(self):
          assert Priority.HIGH > Priority.LOW

      def test_same_priority_equal(self):
          assert Priority.NORMAL == Priority.NORMAL

      def test_from_string_valid(self):
          assert Priority.from_string("high") == Priority.HIGH
          assert Priority.from_string("normal") == Priority.NORMAL
          assert Priority.from_string("low") == Priority.LOW

      def test_from_string_case_insensitive(self):
          assert Priority.from_string("HIGH") == Priority.HIGH
          assert Priority.from_string("Normal") == Priority.NORMAL

      def test_from_string_invalid_raises(self):
          with pytest.raises(ValueError, match="Invalid priority"):
              Priority.from_string("urgent")

      def test_default_is_normal(self):
          assert Priority.default() == Priority.NORMAL

      def test_to_int_ordering(self):
          """Higher priority = higher int value for sorting."""
          assert Priority.HIGH.to_int() > Priority.NORMAL.to_int()
          assert Priority.NORMAL.to_int() > Priority.LOW.to_int()


  class TestPrioritizeTasks:
      def test_sorts_high_before_normal(self):
          tasks = [
              {"id": "T-1", "priority": Priority.NORMAL},
              {"id": "T-2", "priority": Priority.HIGH},
          ]
          result = prioritize_tasks(tasks)
          assert result[0]["id"] == "T-2"
          assert result[1]["id"] == "T-1"

      def test_fifo_within_same_priority(self):
          tasks = [
              {"id": "T-1", "priority": Priority.NORMAL, "enqueued_at": 1000},
              {"id": "T-2", "priority": Priority.NORMAL, "enqueued_at": 999},
          ]
          result = prioritize_tasks(tasks)
          assert result[0]["id"] == "T-2"
          assert result[1]["id"] == "T-1"

      def test_empty_list(self):
          assert prioritize_tasks([]) == []

      def test_mixed_priorities_sorted(self):
          tasks = [
              {"id": "T-1", "priority": Priority.LOW},
              {"id": "T-2", "priority": Priority.HIGH},
              {"id": "T-3", "priority": Priority.NORMAL},
              {"id": "T-4", "priority": Priority.HIGH},
          ]
          result = prioritize_tasks(tasks)
          ids = [t["id"] for t in result]
          assert ids[0] in ("T-2", "T-4")
          assert ids[1] in ("T-2", "T-4")
          assert ids[2] == "T-3"
          assert ids[3] == "T-1"
  ```

  **Run:** `pixi run pytest tests/concurrency/test_priority.py -v`

  **Expect:** All tests fail (module not found).

- [ ] **1b. Implement Priority enum and prioritize_tasks**

  **File:** `src/devteam/concurrency/__init__.py`

  ```python
  """Concurrency management for claude-devteam."""
  ```

  **File:** `src/devteam/concurrency/priority.py`

  ```python
  """Priority levels for jobs and tasks.

  Supports HIGH, NORMAL, LOW with comparison operators and FIFO
  ordering within the same priority level.
  """
  from __future__ import annotations

  from enum import Enum
  from typing import Any


  class Priority(Enum):
      """Task/job priority levels. Higher value = higher priority."""

      HIGH = 3
      NORMAL = 2
      LOW = 1

      def to_int(self) -> int:
          return self.value

      @classmethod
      def from_string(cls, s: str) -> Priority:
          key = s.strip().upper()
          if key not in cls.__members__:
              raise ValueError(
                  f"Invalid priority '{s}'. Must be one of: high, normal, low"
              )
          return cls[key]

      @classmethod
      def default(cls) -> Priority:
          return cls.NORMAL

      def __gt__(self, other: object) -> bool:
          if not isinstance(other, Priority):
              return NotImplemented
          return self.value > other.value

      def __ge__(self, other: object) -> bool:
          if not isinstance(other, Priority):
              return NotImplemented
          return self.value >= other.value

      def __lt__(self, other: object) -> bool:
          if not isinstance(other, Priority):
              return NotImplemented
          return self.value < other.value

      def __le__(self, other: object) -> bool:
          if not isinstance(other, Priority):
              return NotImplemented
          return self.value <= other.value


  def prioritize_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
      """Sort tasks by priority (descending), then by enqueued_at (ascending, FIFO).

      Args:
          tasks: List of task dicts with at least a "priority" key (Priority enum).
                 Optional "enqueued_at" key (numeric timestamp) for FIFO within
                 same priority.

      Returns:
          New sorted list. Original list is not modified.
      """
      return sorted(
          tasks,
          key=lambda t: (
              -t["priority"].to_int(),
              t.get("enqueued_at", 0),
          ),
      )
  ```

  **Run:** `pixi run pytest tests/concurrency/test_priority.py -v`

  **Expect:** All tests pass.

---

## Task 2: Global Pause Flag (Rate Limit Coordination)

**File:** `src/devteam/concurrency/rate_limit.py`

### Steps

- [ ] **2a. Write failing tests for global pause flag read/write/clear**

  **File:** `tests/concurrency/test_rate_limit.py`

  ```python
  """Tests for rate limit detection and global pause flag."""
  import sqlite3
  import time
  from unittest.mock import patch, MagicMock

  import pytest
  from devteam.concurrency.rate_limit import (
      init_pause_table,
      set_global_pause,
      get_global_pause,
      clear_global_pause,
      is_paused,
      check_pause_before_invoke,
      handle_rate_limit_error,
      PauseStatus,
      DEFAULT_BACKOFF_SECONDS,
  )


  @pytest.fixture
  def db(tmp_path):
      """Create a fresh SQLite database with the pause table."""
      db_path = str(tmp_path / "test.sqlite")
      conn = sqlite3.connect(db_path)
      init_pause_table(conn)
      yield conn
      conn.close()


  class TestGlobalPauseFlag:
      def test_no_pause_initially(self, db):
          assert is_paused(db) is False

      def test_set_pause_makes_paused(self, db):
          set_global_pause(db, seconds=60)
          assert is_paused(db) is True

      def test_get_pause_returns_resume_time(self, db):
          set_global_pause(db, seconds=120)
          pause = get_global_pause(db)
          assert pause is not None
          assert pause.resume_at > time.time()
          assert pause.resume_at <= time.time() + 121  # small tolerance

      def test_get_pause_returns_none_when_not_paused(self, db):
          assert get_global_pause(db) is None

      def test_clear_pause(self, db):
          set_global_pause(db, seconds=60)
          assert is_paused(db) is True
          clear_global_pause(db)
          assert is_paused(db) is False

      def test_expired_pause_is_not_paused(self, db):
          set_global_pause(db, seconds=0)
          # Pause with 0 seconds is immediately expired
          assert is_paused(db) is False

      def test_set_pause_overwrites_existing(self, db):
          set_global_pause(db, seconds=60)
          set_global_pause(db, seconds=300)
          pause = get_global_pause(db)
          assert pause is not None
          # Should be ~300 seconds from now, not 60
          assert pause.resume_at > time.time() + 200

      def test_pause_status_remaining_seconds(self, db):
          set_global_pause(db, seconds=120)
          pause = get_global_pause(db)
          assert pause is not None
          remaining = pause.remaining_seconds()
          assert 118 <= remaining <= 121

      def test_pause_status_remaining_seconds_expired(self, db):
          set_global_pause(db, seconds=0)
          pause = get_global_pause(db)
          # expired pauses return None from get_global_pause
          assert pause is None


  class TestCheckPauseBeforeInvoke:
      def test_returns_not_paused_when_clear(self, db):
          result = check_pause_before_invoke(db)
          assert result.paused is False
          assert result.resume_at is None

      def test_returns_paused_with_resume_time(self, db):
          set_global_pause(db, seconds=90)
          result = check_pause_before_invoke(db)
          assert result.paused is True
          assert result.resume_at is not None
          assert result.resume_at > time.time()


  class TestHandleRateLimitError:
      def test_parses_reset_time_from_error(self, db):
          error = Exception("Rate limit exceeded. Retry after 1800 seconds.")
          seconds = handle_rate_limit_error(db, error)
          assert seconds == 1800

      def test_uses_default_when_unparseable(self, db):
          error = Exception("Rate limit exceeded.")
          seconds = handle_rate_limit_error(db, error)
          assert seconds == DEFAULT_BACKOFF_SECONDS

      def test_sets_global_pause_on_handle(self, db):
          error = Exception("Rate limit exceeded. Retry after 600 seconds.")
          handle_rate_limit_error(db, error)
          assert is_paused(db) is True
          pause = get_global_pause(db)
          assert pause is not None
          assert pause.remaining_seconds() > 500

      def test_handles_anthropic_rate_limit_format(self, db):
          """Test parsing of 'retry-after: 120' header-style message."""
          error = Exception(
              "anthropic.RateLimitError: retry-after: 120"
          )
          seconds = handle_rate_limit_error(db, error)
          assert seconds == 120
  ```

  **Run:** `pixi run pytest tests/concurrency/test_rate_limit.py -v`

  **Expect:** All tests fail (module not found).

- [ ] **2b. Implement global pause flag and rate limit handling**

  **File:** `src/devteam/concurrency/rate_limit.py`

  ```python
  """Rate limit detection and global pause flag.

  The global pause flag is a single row in SQLite that all workflows check
  before dispatching agent invocations. When any workflow hits a rate limit,
  it sets the flag so all workflows pause together.
  """
  from __future__ import annotations

  import re
  import sqlite3
  import time
  from dataclasses import dataclass
  from typing import Optional


  DEFAULT_BACKOFF_SECONDS = 1800  # 30 minutes, from config.toml default


  @dataclass
  class PauseStatus:
      """Current state of the global pause flag."""

      resume_at: float  # unix timestamp

      def remaining_seconds(self) -> float:
          return max(0.0, self.resume_at - time.time())

      def is_expired(self) -> bool:
          return time.time() >= self.resume_at


  @dataclass
  class PauseCheckResult:
      """Result of checking the pause flag before an invocation."""

      paused: bool
      resume_at: Optional[float] = None


  def init_pause_table(conn: sqlite3.Connection) -> None:
      """Create the global_pause table if it doesn't exist."""
      conn.execute("""
          CREATE TABLE IF NOT EXISTS global_pause (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              resume_at REAL NOT NULL,
              set_at REAL NOT NULL,
              reason TEXT
          )
      """)
      conn.commit()


  def set_global_pause(
      conn: sqlite3.Connection,
      seconds: float,
      reason: str = "rate_limit",
  ) -> float:
      """Set the global pause flag. Returns the resume_at timestamp.

      Uses INSERT OR REPLACE to ensure only one row exists (id=1).
      """
      now = time.time()
      resume_at = now + seconds
      conn.execute(
          """
          INSERT OR REPLACE INTO global_pause (id, resume_at, set_at, reason)
          VALUES (1, ?, ?, ?)
          """,
          (resume_at, now, reason),
      )
      conn.commit()
      return resume_at


  def get_global_pause(conn: sqlite3.Connection) -> Optional[PauseStatus]:
      """Get the current pause status. Returns None if not paused or expired."""
      row = conn.execute(
          "SELECT resume_at FROM global_pause WHERE id = 1"
      ).fetchone()
      if row is None:
          return None
      status = PauseStatus(resume_at=row[0])
      if status.is_expired():
          # Auto-clear expired pauses
          clear_global_pause(conn)
          return None
      return status


  def clear_global_pause(conn: sqlite3.Connection) -> None:
      """Clear the global pause flag."""
      conn.execute("DELETE FROM global_pause WHERE id = 1")
      conn.commit()


  def is_paused(conn: sqlite3.Connection) -> bool:
      """Check if the system is currently paused."""
      return get_global_pause(conn) is not None


  def check_pause_before_invoke(conn: sqlite3.Connection) -> PauseCheckResult:
      """Check the pause flag before dispatching an agent invocation.

      This is called by every workflow before each agent invocation.
      When one workflow sets the pause flag, all workflows see it.
      """
      pause = get_global_pause(conn)
      if pause is None:
          return PauseCheckResult(paused=False)
      return PauseCheckResult(paused=True, resume_at=pause.resume_at)


  def _parse_reset_seconds(error: Exception) -> Optional[int]:
      """Extract the reset/retry time in seconds from a rate limit error.

      Handles formats:
          - "Retry after 1800 seconds"
          - "retry-after: 120"
          - "Retry after 1800 seconds."
      """
      msg = str(error)
      # Pattern: "Retry after N seconds"
      match = re.search(r"[Rr]etry\s+after\s+(\d+)\s+seconds", msg)
      if match:
          return int(match.group(1))
      # Pattern: "retry-after: N"
      match = re.search(r"retry-after:\s*(\d+)", msg, re.IGNORECASE)
      if match:
          return int(match.group(1))
      return None


  def handle_rate_limit_error(
      conn: sqlite3.Connection,
      error: Exception,
  ) -> int:
      """Handle a rate limit error by setting the global pause flag.

      Parses the error message to extract the retry time. Falls back to
      DEFAULT_BACKOFF_SECONDS if the error message can't be parsed.

      Returns the number of seconds to wait.
      """
      seconds = _parse_reset_seconds(error) or DEFAULT_BACKOFF_SECONDS
      set_global_pause(conn, seconds=seconds, reason="rate_limit")
      return seconds
  ```

  **Run:** `pixi run pytest tests/concurrency/test_rate_limit.py -v`

  **Expect:** All tests pass.

---

## Task 3: DBOS Queue Setup with Concurrency Limit

**File:** `src/devteam/concurrency/queue.py`

### Steps

- [ ] **3a. Write failing tests for queue creation and enqueue logic**

  **File:** `tests/concurrency/test_queue.py`

  ```python
  """Tests for DBOS queue setup and enqueue logic."""
  import sqlite3
  from unittest.mock import patch, MagicMock, PropertyMock

  import pytest
  from devteam.concurrency.priority import Priority
  from devteam.concurrency.queue import (
      AgentQueueConfig,
      AgentQueueItem,
      create_agent_queue_config,
      enqueue_agent_invocation,
      dequeue_next,
      get_queue_depth,
      get_active_count,
      init_queue_table,
  )


  @pytest.fixture
  def db(tmp_path):
      """Create a fresh SQLite database with queue tables."""
      db_path = str(tmp_path / "test.sqlite")
      conn = sqlite3.connect(db_path)
      init_queue_table(conn)
      yield conn
      conn.close()


  class TestAgentQueueConfig:
      def test_default_concurrency(self):
          config = create_agent_queue_config()
          assert config.max_concurrent == 3

      def test_custom_concurrency(self):
          config = create_agent_queue_config(max_concurrent=5)
          assert config.max_concurrent == 5

      def test_concurrency_must_be_positive(self):
          with pytest.raises(ValueError, match="must be positive"):
              create_agent_queue_config(max_concurrent=0)

      def test_concurrency_from_config_dict(self):
          config_dict = {"general": {"max_concurrent_agents": 8}}
          config = create_agent_queue_config(
              max_concurrent=config_dict["general"]["max_concurrent_agents"]
          )
          assert config.max_concurrent == 8


  class TestEnqueueAndDequeue:
      def test_enqueue_creates_item(self, db):
          enqueue_agent_invocation(
              db,
              job_id="W-1",
              task_id="T-1",
              role="backend",
              priority=Priority.NORMAL,
          )
          assert get_queue_depth(db) == 1

      def test_dequeue_returns_highest_priority(self, db):
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-1",
              role="backend", priority=Priority.NORMAL,
          )
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-2",
              role="frontend", priority=Priority.HIGH,
          )
          item = dequeue_next(db, max_concurrent=3)
          assert item is not None
          assert item.task_id == "T-2"
          assert item.priority == Priority.HIGH

      def test_dequeue_fifo_within_priority(self, db):
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-1",
              role="backend", priority=Priority.NORMAL,
          )
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-2",
              role="frontend", priority=Priority.NORMAL,
          )
          item = dequeue_next(db, max_concurrent=3)
          assert item is not None
          assert item.task_id == "T-1"

      def test_dequeue_returns_none_when_empty(self, db):
          item = dequeue_next(db, max_concurrent=3)
          assert item is None

      def test_dequeue_respects_concurrency_limit(self, db):
          # Enqueue 4 items
          for i in range(4):
              enqueue_agent_invocation(
                  db, job_id="W-1", task_id=f"T-{i}",
                  role="backend", priority=Priority.NORMAL,
              )
          # Dequeue 3 (the limit)
          items = []
          for _ in range(3):
              item = dequeue_next(db, max_concurrent=3)
              assert item is not None
              items.append(item)
          # 4th dequeue should return None (at concurrency limit)
          item = dequeue_next(db, max_concurrent=3)
          assert item is None

      def test_dequeue_slot_freed_after_complete(self, db):
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-1",
              role="backend", priority=Priority.NORMAL,
          )
          item = dequeue_next(db, max_concurrent=1)
          assert item is not None
          # Mark as complete
          item.mark_complete(db)
          # Now another item can be dequeued
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-2",
              role="frontend", priority=Priority.NORMAL,
          )
          item2 = dequeue_next(db, max_concurrent=1)
          assert item2 is not None
          assert item2.task_id == "T-2"


  class TestQueueDepthAndActive:
      def test_queue_depth_counts_pending(self, db):
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-1",
              role="backend", priority=Priority.NORMAL,
          )
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-2",
              role="frontend", priority=Priority.NORMAL,
          )
          assert get_queue_depth(db) == 2

      def test_active_count_tracks_running(self, db):
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-1",
              role="backend", priority=Priority.NORMAL,
          )
          assert get_active_count(db) == 0
          item = dequeue_next(db, max_concurrent=3)
          assert item is not None
          assert get_active_count(db) == 1

      def test_active_count_decreases_on_complete(self, db):
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-1",
              role="backend", priority=Priority.NORMAL,
          )
          item = dequeue_next(db, max_concurrent=3)
          assert item is not None
          assert get_active_count(db) == 1
          item.mark_complete(db)
          assert get_active_count(db) == 0


  class TestMultiJobFairness:
      def test_different_jobs_share_queue(self, db):
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-1",
              role="backend", priority=Priority.NORMAL,
          )
          enqueue_agent_invocation(
              db, job_id="W-2", task_id="T-1",
              role="frontend", priority=Priority.NORMAL,
          )
          assert get_queue_depth(db) == 2

      def test_high_priority_job_tasks_dequeued_first(self, db):
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-1",
              role="backend", priority=Priority.NORMAL,
          )
          enqueue_agent_invocation(
              db, job_id="W-2", task_id="T-1",
              role="frontend", priority=Priority.HIGH,
          )
          item = dequeue_next(db, max_concurrent=3)
          assert item is not None
          assert item.job_id == "W-2"
  ```

  **Run:** `pixi run pytest tests/concurrency/test_queue.py -v`

  **Expect:** All tests fail (module not found).

- [ ] **3b. Implement queue table, enqueue, dequeue, and concurrency tracking**

  **File:** `src/devteam/concurrency/queue.py`

  ```python
  """DBOS-compatible agent invocation queue.

  All jobs submit to a single shared queue. The queue enforces a
  concurrency limit (max_concurrent_agents from config.toml) and
  dequeues by priority then FIFO.

  In the full system this wraps DBOS Queue. For testability and
  the plan implementation phase, we use a SQLite-backed queue with
  the same semantics that will be swapped for DBOS Queue in
  integration.
  """
  from __future__ import annotations

  import sqlite3
  import time
  from dataclasses import dataclass
  from typing import Optional

  from devteam.concurrency.priority import Priority


  # Queue item states
  PENDING = "pending"
  ACTIVE = "active"
  COMPLETED = "completed"
  FAILED = "failed"


  @dataclass
  class AgentQueueConfig:
      """Configuration for the agent invocation queue."""

      max_concurrent: int

      def __post_init__(self) -> None:
          if self.max_concurrent <= 0:
              raise ValueError("max_concurrent must be positive")


  @dataclass
  class AgentQueueItem:
      """An item in the agent invocation queue."""

      id: int
      job_id: str
      task_id: str
      role: str
      priority: Priority
      status: str
      enqueued_at: float

      def mark_complete(self, conn: sqlite3.Connection) -> None:
          """Mark this queue item as completed, freeing the concurrency slot."""
          conn.execute(
              "UPDATE agent_queue SET status = ? WHERE id = ?",
              (COMPLETED, self.id),
          )
          conn.commit()

      def mark_failed(self, conn: sqlite3.Connection) -> None:
          """Mark this queue item as failed, freeing the concurrency slot."""
          conn.execute(
              "UPDATE agent_queue SET status = ? WHERE id = ?",
              (FAILED, self.id),
          )
          conn.commit()


  def create_agent_queue_config(max_concurrent: int = 3) -> AgentQueueConfig:
      """Create queue configuration. Default matches spec: 3 concurrent agents."""
      return AgentQueueConfig(max_concurrent=max_concurrent)


  def init_queue_table(conn: sqlite3.Connection) -> None:
      """Create the agent_queue table if it doesn't exist."""
      conn.execute("""
          CREATE TABLE IF NOT EXISTS agent_queue (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              role TEXT NOT NULL,
              priority INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              enqueued_at REAL NOT NULL,
              started_at REAL,
              completed_at REAL
          )
      """)
      conn.execute("""
          CREATE INDEX IF NOT EXISTS idx_queue_status
          ON agent_queue (status)
      """)
      conn.execute("""
          CREATE INDEX IF NOT EXISTS idx_queue_priority
          ON agent_queue (priority DESC, enqueued_at ASC)
      """)
      conn.commit()


  def enqueue_agent_invocation(
      conn: sqlite3.Connection,
      job_id: str,
      task_id: str,
      role: str,
      priority: Priority,
  ) -> int:
      """Add an agent invocation to the queue. Returns the queue item ID."""
      cursor = conn.execute(
          """
          INSERT INTO agent_queue (job_id, task_id, role, priority, status, enqueued_at)
          VALUES (?, ?, ?, ?, ?, ?)
          """,
          (job_id, task_id, role, priority.to_int(), PENDING, time.time()),
      )
      conn.commit()
      return cursor.lastrowid  # type: ignore[return-value]


  def dequeue_next(
      conn: sqlite3.Connection,
      max_concurrent: int,
  ) -> Optional[AgentQueueItem]:
      """Dequeue the highest-priority pending item, respecting concurrency limit.

      Returns None if no items are pending or the concurrency limit is reached.
      """
      # Check active count
      active = get_active_count(conn)
      if active >= max_concurrent:
          return None

      # Get highest-priority pending item (priority DESC, enqueued_at ASC = FIFO)
      row = conn.execute(
          """
          SELECT id, job_id, task_id, role, priority, status, enqueued_at
          FROM agent_queue
          WHERE status = ?
          ORDER BY priority DESC, enqueued_at ASC
          LIMIT 1
          """,
          (PENDING,),
      ).fetchone()

      if row is None:
          return None

      item = AgentQueueItem(
          id=row[0],
          job_id=row[1],
          task_id=row[2],
          role=row[3],
          priority=Priority(row[4]),
          status=ACTIVE,
          enqueued_at=row[6],
      )

      # Atomically mark as active
      conn.execute(
          "UPDATE agent_queue SET status = ?, started_at = ? WHERE id = ?",
          (ACTIVE, time.time(), item.id),
      )
      conn.commit()

      return item


  def get_queue_depth(conn: sqlite3.Connection) -> int:
      """Count of pending items in the queue."""
      row = conn.execute(
          "SELECT COUNT(*) FROM agent_queue WHERE status = ?",
          (PENDING,),
      ).fetchone()
      return row[0] if row else 0


  def get_active_count(conn: sqlite3.Connection) -> int:
      """Count of currently active (running) items."""
      row = conn.execute(
          "SELECT COUNT(*) FROM agent_queue WHERE status = ?",
          (ACTIVE,),
      ).fetchone()
      return row[0] if row else 0
  ```

  **Run:** `pixi run pytest tests/concurrency/test_queue.py -v`

  **Expect:** All tests pass.

---

## Task 4: Approval Gates System

**File:** `src/devteam/concurrency/approval.py`

### Steps

- [ ] **4a. Write failing tests for approval gate configuration and checking**

  **File:** `tests/concurrency/test_approval.py`

  ```python
  """Tests for configurable approval gates."""
  import pytest
  from devteam.concurrency.approval import (
      ApprovalPolicy,
      ApprovalGates,
      ApprovalDecision,
      load_approval_gates,
      check_approval,
      DEFAULT_GATES,
  )


  class TestApprovalPolicy:
      def test_auto_policy(self):
          assert ApprovalPolicy.AUTO.is_auto() is True
          assert ApprovalPolicy.AUTO.is_manual() is False
          assert ApprovalPolicy.AUTO.is_never() is False

      def test_manual_policy(self):
          assert ApprovalPolicy.MANUAL.is_manual() is True
          assert ApprovalPolicy.MANUAL.is_auto() is False

      def test_never_policy(self):
          assert ApprovalPolicy.NEVER.is_never() is True
          assert ApprovalPolicy.NEVER.is_auto() is False

      def test_from_string(self):
          assert ApprovalPolicy.from_string("auto") == ApprovalPolicy.AUTO
          assert ApprovalPolicy.from_string("manual") == ApprovalPolicy.MANUAL
          assert ApprovalPolicy.from_string("never") == ApprovalPolicy.NEVER

      def test_from_string_invalid(self):
          with pytest.raises(ValueError, match="Invalid approval policy"):
              ApprovalPolicy.from_string("sometimes")


  class TestApprovalGates:
      def test_default_gates_match_spec(self):
          """Verify defaults match config.toml spec."""
          gates = DEFAULT_GATES
          assert gates.commit == ApprovalPolicy.AUTO
          assert gates.push == ApprovalPolicy.AUTO
          assert gates.open_pr == ApprovalPolicy.AUTO
          assert gates.merge == ApprovalPolicy.AUTO
          assert gates.cleanup == ApprovalPolicy.AUTO
          assert gates.push_to_main == ApprovalPolicy.NEVER

      def test_load_from_config_dict(self):
          config = {
              "approval": {
                  "commit": "auto",
                  "push": "auto",
                  "open_pr": "auto",
                  "merge": "manual",
                  "cleanup": "auto",
                  "push_to_main": "never",
              }
          }
          gates = load_approval_gates(config)
          assert gates.merge == ApprovalPolicy.MANUAL
          assert gates.commit == ApprovalPolicy.AUTO

      def test_load_partial_config_uses_defaults(self):
          config = {"approval": {"merge": "manual"}}
          gates = load_approval_gates(config)
          assert gates.merge == ApprovalPolicy.MANUAL
          assert gates.commit == ApprovalPolicy.AUTO  # default
          assert gates.push_to_main == ApprovalPolicy.NEVER  # always never

      def test_push_to_main_forced_to_never(self):
          """push_to_main is ALWAYS never regardless of config."""
          config = {"approval": {"push_to_main": "auto"}}
          gates = load_approval_gates(config)
          assert gates.push_to_main == ApprovalPolicy.NEVER


  class TestCheckApproval:
      def test_auto_action_approved_immediately(self):
          gates = DEFAULT_GATES
          decision = check_approval(gates, "commit")
          assert decision.approved is True
          assert decision.needs_human is False

      def test_manual_action_needs_human(self):
          gates = ApprovalGates(
              commit=ApprovalPolicy.AUTO,
              push=ApprovalPolicy.AUTO,
              open_pr=ApprovalPolicy.AUTO,
              merge=ApprovalPolicy.MANUAL,
              cleanup=ApprovalPolicy.AUTO,
              push_to_main=ApprovalPolicy.NEVER,
          )
          decision = check_approval(gates, "merge")
          assert decision.approved is False
          assert decision.needs_human is True

      def test_never_action_blocked(self):
          gates = DEFAULT_GATES
          decision = check_approval(gates, "push_to_main")
          assert decision.approved is False
          assert decision.needs_human is False
          assert decision.blocked is True

      def test_unknown_action_raises(self):
          gates = DEFAULT_GATES
          with pytest.raises(ValueError, match="Unknown action"):
              check_approval(gates, "deploy")

      def test_check_all_valid_actions(self):
          gates = DEFAULT_GATES
          valid_actions = ["commit", "push", "open_pr", "merge", "cleanup", "push_to_main"]
          for action in valid_actions:
              decision = check_approval(gates, action)
              assert isinstance(decision, ApprovalDecision)
  ```

  **Run:** `pixi run pytest tests/concurrency/test_approval.py -v`

  **Expect:** All tests fail (module not found).

- [ ] **4b. Implement approval gates**

  **File:** `src/devteam/concurrency/approval.py`

  ```python
  """Configurable approval gates for side-effecting actions.

  Each action (commit, push, open_pr, merge, cleanup) has a policy:
  - auto: proceed without human intervention
  - manual: pause and wait for human approval
  - never: hard block, action is forbidden

  push_to_main is always "never" regardless of configuration.
  """
  from __future__ import annotations

  from dataclasses import dataclass
  from enum import Enum
  from typing import Any


  class ApprovalPolicy(Enum):
      """Policy for a side-effecting action."""

      AUTO = "auto"
      MANUAL = "manual"
      NEVER = "never"

      def is_auto(self) -> bool:
          return self == ApprovalPolicy.AUTO

      def is_manual(self) -> bool:
          return self == ApprovalPolicy.MANUAL

      def is_never(self) -> bool:
          return self == ApprovalPolicy.NEVER

      @classmethod
      def from_string(cls, s: str) -> ApprovalPolicy:
          key = s.strip().lower()
          for member in cls:
              if member.value == key:
                  return member
          raise ValueError(
              f"Invalid approval policy '{s}'. Must be one of: auto, manual, never"
          )


  @dataclass
  class ApprovalGates:
      """Approval policies for all side-effecting actions."""

      commit: ApprovalPolicy
      push: ApprovalPolicy
      open_pr: ApprovalPolicy
      merge: ApprovalPolicy
      cleanup: ApprovalPolicy
      push_to_main: ApprovalPolicy


  @dataclass
  class ApprovalDecision:
      """Result of checking an approval gate."""

      approved: bool
      needs_human: bool
      blocked: bool
      action: str
      policy: ApprovalPolicy


  # Spec defaults from config.toml
  DEFAULT_GATES = ApprovalGates(
      commit=ApprovalPolicy.AUTO,
      push=ApprovalPolicy.AUTO,
      open_pr=ApprovalPolicy.AUTO,
      merge=ApprovalPolicy.AUTO,
      cleanup=ApprovalPolicy.AUTO,
      push_to_main=ApprovalPolicy.NEVER,
  )

  # Actions that map to ApprovalGates fields
  VALID_ACTIONS = {"commit", "push", "open_pr", "merge", "cleanup", "push_to_main"}


  def load_approval_gates(config: dict[str, Any]) -> ApprovalGates:
      """Load approval gates from a config dict (parsed from config.toml).

      Missing keys fall back to defaults. push_to_main is always forced
      to NEVER regardless of what the config says.
      """
      approval_section = config.get("approval", {})

      def _get_policy(key: str, default: ApprovalPolicy) -> ApprovalPolicy:
          value = approval_section.get(key)
          if value is None:
              return default
          return ApprovalPolicy.from_string(value)

      gates = ApprovalGates(
          commit=_get_policy("commit", DEFAULT_GATES.commit),
          push=_get_policy("push", DEFAULT_GATES.push),
          open_pr=_get_policy("open_pr", DEFAULT_GATES.open_pr),
          merge=_get_policy("merge", DEFAULT_GATES.merge),
          cleanup=_get_policy("cleanup", DEFAULT_GATES.cleanup),
          push_to_main=ApprovalPolicy.NEVER,  # ALWAYS never, hard block
      )
      return gates


  def check_approval(gates: ApprovalGates, action: str) -> ApprovalDecision:
      """Check whether an action is approved, needs human approval, or is blocked.

      Args:
          gates: The current approval gate configuration.
          action: One of: commit, push, open_pr, merge, cleanup, push_to_main.

      Returns:
          ApprovalDecision with the verdict.

      Raises:
          ValueError: If action is not a recognized gate.
      """
      if action not in VALID_ACTIONS:
          raise ValueError(
              f"Unknown action '{action}'. Must be one of: {', '.join(sorted(VALID_ACTIONS))}"
          )

      policy: ApprovalPolicy = getattr(gates, action)

      if policy.is_auto():
          return ApprovalDecision(
              approved=True,
              needs_human=False,
              blocked=False,
              action=action,
              policy=policy,
          )
      elif policy.is_manual():
          return ApprovalDecision(
              approved=False,
              needs_human=True,
              blocked=False,
              action=action,
              policy=policy,
          )
      else:  # NEVER
          return ApprovalDecision(
              approved=False,
              needs_human=False,
              blocked=True,
              action=action,
              policy=policy,
          )
  ```

  **Run:** `pixi run pytest tests/concurrency/test_approval.py -v`

  **Expect:** All tests pass.

---

## Task 5: Durable Sleep Persistence Across Restart

**File:** `tests/concurrency/test_durable_sleep.py`

### Steps

- [ ] **5a. Write failing tests for durable sleep persistence simulation**

  **File:** `tests/concurrency/test_durable_sleep.py`

  ```python
  """Tests for durable sleep behavior across simulated restart.

  Verifies that the global pause flag persists in SQLite and survives
  a simulated process restart (close + reopen connection).
  """
  import sqlite3
  import time

  import pytest
  from devteam.concurrency.rate_limit import (
      init_pause_table,
      set_global_pause,
      get_global_pause,
      is_paused,
      clear_global_pause,
  )


  @pytest.fixture
  def db_path(tmp_path):
      """Return path to a temporary SQLite database."""
      return str(tmp_path / "durable_test.sqlite")


  class TestDurableSleepPersistence:
      def test_pause_survives_connection_close(self, db_path):
          """Simulate crash: set pause, close connection, reopen, verify pause."""
          # Process 1: set pause
          conn1 = sqlite3.connect(db_path)
          init_pause_table(conn1)
          set_global_pause(conn1, seconds=600)
          conn1.close()

          # Process 2: reopen (simulating daemon restart)
          conn2 = sqlite3.connect(db_path)
          # Table already exists, init is idempotent
          init_pause_table(conn2)
          assert is_paused(conn2) is True
          pause = get_global_pause(conn2)
          assert pause is not None
          assert pause.remaining_seconds() > 500
          conn2.close()

      def test_expired_pause_cleared_after_restart(self, db_path):
          """Pause that expired during downtime is cleared on read."""
          conn1 = sqlite3.connect(db_path)
          init_pause_table(conn1)
          # Set a pause that's already expired (0 seconds)
          set_global_pause(conn1, seconds=0)
          conn1.close()

          conn2 = sqlite3.connect(db_path)
          init_pause_table(conn2)
          assert is_paused(conn2) is False
          conn2.close()

      def test_clear_pause_persists_after_restart(self, db_path):
          """Clearing pause before crash means it stays clear after restart."""
          conn1 = sqlite3.connect(db_path)
          init_pause_table(conn1)
          set_global_pause(conn1, seconds=600)
          clear_global_pause(conn1)
          conn1.close()

          conn2 = sqlite3.connect(db_path)
          init_pause_table(conn2)
          assert is_paused(conn2) is False
          conn2.close()

      def test_resume_time_accurate_after_restart(self, db_path):
          """Resume time is an absolute timestamp, not relative to restart."""
          conn1 = sqlite3.connect(db_path)
          init_pause_table(conn1)
          resume_at = set_global_pause(conn1, seconds=300)
          conn1.close()

          conn2 = sqlite3.connect(db_path)
          init_pause_table(conn2)
          pause = get_global_pause(conn2)
          assert pause is not None
          # resume_at should be the same absolute timestamp
          assert abs(pause.resume_at - resume_at) < 1.0
          conn2.close()

      def test_multiple_workflows_see_same_pause(self, db_path):
          """Multiple connections (simulating multiple workflows) see the same flag."""
          conn_writer = sqlite3.connect(db_path)
          init_pause_table(conn_writer)
          set_global_pause(conn_writer, seconds=120)

          # Two reader connections (simulating concurrent workflows)
          conn_reader1 = sqlite3.connect(db_path)
          conn_reader2 = sqlite3.connect(db_path)

          assert is_paused(conn_reader1) is True
          assert is_paused(conn_reader2) is True

          pause1 = get_global_pause(conn_reader1)
          pause2 = get_global_pause(conn_reader2)
          assert pause1 is not None
          assert pause2 is not None
          assert abs(pause1.resume_at - pause2.resume_at) < 0.01

          conn_writer.close()
          conn_reader1.close()
          conn_reader2.close()
  ```

  **Run:** `pixi run pytest tests/concurrency/test_durable_sleep.py -v`

  **Expect:** All tests pass (uses already-implemented rate_limit module).

---

## Task 6: Rate-Limit-Aware Agent Invocation Wrapper

**File:** `tests/concurrency/test_rate_limit_invoke.py`

### Steps

- [ ] **6a. Write failing tests for rate-limit-aware invocation**

  **File:** `tests/concurrency/test_rate_limit_invoke.py`

  ```python
  """Tests for rate-limit-aware agent invocation.

  Mocks the Agent SDK to simulate RateLimitError and verifies the
  orchestrator correctly sets the global pause, waits, clears, and retries.
  """
  import sqlite3
  from unittest.mock import MagicMock, patch, call

  import pytest
  from devteam.concurrency.rate_limit import (
      init_pause_table,
      is_paused,
      get_global_pause,
      DEFAULT_BACKOFF_SECONDS,
  )
  from devteam.concurrency.queue import init_queue_table
  from devteam.concurrency.invoke import (
      rate_limit_aware_invoke,
      RateLimitError,
  )


  @pytest.fixture
  def db(tmp_path):
      db_path = str(tmp_path / "test.sqlite")
      conn = sqlite3.connect(db_path)
      init_pause_table(conn)
      init_queue_table(conn)
      yield conn
      conn.close()


  class TestRateLimitAwareInvoke:
      def test_successful_invocation_no_pause(self, db):
          """Normal invocation sets no pause."""
          mock_invoke = MagicMock(return_value={"status": "completed"})
          result = rate_limit_aware_invoke(
              db=db,
              invoke_fn=mock_invoke,
              role="backend",
              task_id="T-1",
              context="Build the API",
          )
          assert result == {"status": "completed"}
          assert is_paused(db) is False
          mock_invoke.assert_called_once()

      def test_rate_limit_sets_pause_and_retries(self, db):
          """On RateLimitError, sets pause flag and retries after clear."""
          error = RateLimitError("Rate limit exceeded. Retry after 60 seconds.")
          mock_invoke = MagicMock(
              side_effect=[error, {"status": "completed"}]
          )
          mock_sleep = MagicMock()

          result = rate_limit_aware_invoke(
              db=db,
              invoke_fn=mock_invoke,
              role="backend",
              task_id="T-1",
              context="Build the API",
              sleep_fn=mock_sleep,
          )

          assert result == {"status": "completed"}
          assert mock_invoke.call_count == 2
          mock_sleep.assert_called_once_with(60)

      def test_rate_limit_uses_default_backoff(self, db):
          """Unparseable error uses default backoff."""
          error = RateLimitError("Rate limit exceeded.")
          mock_invoke = MagicMock(
              side_effect=[error, {"status": "completed"}]
          )
          mock_sleep = MagicMock()

          rate_limit_aware_invoke(
              db=db,
              invoke_fn=mock_invoke,
              role="backend",
              task_id="T-1",
              context="Build the API",
              sleep_fn=mock_sleep,
          )

          mock_sleep.assert_called_once_with(DEFAULT_BACKOFF_SECONDS)

      def test_pause_flag_set_during_backoff(self, db):
          """Global pause flag is set when rate limit is hit."""
          error = RateLimitError("Rate limit exceeded. Retry after 300 seconds.")
          pause_was_set = False

          def mock_invoke_fn(*args, **kwargs):
              nonlocal pause_was_set
              if not pause_was_set:
                  raise error
              return {"status": "completed"}

          def mock_sleep_fn(seconds):
              nonlocal pause_was_set
              # During sleep, the pause flag should be set
              assert is_paused(db) is True
              pause = get_global_pause(db)
              assert pause is not None
              assert pause.remaining_seconds() > 200
              pause_was_set = True

          rate_limit_aware_invoke(
              db=db,
              invoke_fn=mock_invoke_fn,
              role="backend",
              task_id="T-1",
              context="Build the API",
              sleep_fn=mock_sleep_fn,
          )

      def test_pause_cleared_after_retry(self, db):
          """Pause flag is cleared after successful retry."""
          error = RateLimitError("Rate limit exceeded. Retry after 10 seconds.")
          mock_invoke = MagicMock(
              side_effect=[error, {"status": "completed"}]
          )
          mock_sleep = MagicMock()

          rate_limit_aware_invoke(
              db=db,
              invoke_fn=mock_invoke,
              role="backend",
              task_id="T-1",
              context="Build the API",
              sleep_fn=mock_sleep,
          )

          assert is_paused(db) is False

      def test_respects_existing_pause(self, db):
          """If already paused (by another workflow), waits for that pause."""
          from devteam.concurrency.rate_limit import set_global_pause
          import time

          set_global_pause(db, seconds=30)
          mock_invoke = MagicMock(return_value={"status": "completed"})
          mock_sleep = MagicMock()

          result = rate_limit_aware_invoke(
              db=db,
              invoke_fn=mock_invoke,
              role="backend",
              task_id="T-1",
              context="Build the API",
              sleep_fn=mock_sleep,
          )

          # Should have slept for the existing pause duration
          assert mock_sleep.call_count == 1
          sleep_seconds = mock_sleep.call_args[0][0]
          assert 25 <= sleep_seconds <= 31
          # Then invoked successfully
          assert result == {"status": "completed"}
  ```

  **Run:** `pixi run pytest tests/concurrency/test_rate_limit_invoke.py -v`

  **Expect:** All tests fail (module not found).

- [ ] **6b. Implement rate-limit-aware invocation wrapper**

  **File:** `src/devteam/concurrency/invoke.py`

  ```python
  """Rate-limit-aware agent invocation wrapper.

  Wraps agent SDK calls with:
  1. Pre-invocation pause check (respect global pause from other workflows)
  2. RateLimitError catch with parse, pause, sleep, retry
  3. Post-retry pause clear

  In the full system, sleep_fn maps to DBOS.sleep() for durable sleep.
  For testing, sleep_fn is injectable.
  """
  from __future__ import annotations

  import time
  from typing import Any, Callable, Optional

  import sqlite3

  from devteam.concurrency.rate_limit import (
      check_pause_before_invoke,
      handle_rate_limit_error,
      clear_global_pause,
      DEFAULT_BACKOFF_SECONDS,
  )


  class RateLimitError(Exception):
      """Raised when the Agent SDK hits an API rate limit."""

      pass


  def _default_sleep(seconds: float) -> None:
      """Default sleep function. Replaced by DBOS.sleep() in production."""
      time.sleep(seconds)


  def rate_limit_aware_invoke(
      db: sqlite3.Connection,
      invoke_fn: Callable[..., Any],
      role: str,
      task_id: str,
      context: str,
      sleep_fn: Optional[Callable[[float], None]] = None,
  ) -> Any:
      """Invoke an agent with rate limit awareness.

      1. Check if globally paused (another workflow hit a limit) — if so, wait.
      2. Call invoke_fn.
      3. On RateLimitError: set global pause, sleep, clear pause, retry once.

      Args:
          db: SQLite connection for pause flag reads/writes.
          invoke_fn: The actual agent invocation function (Agent SDK query).
          role: Agent role being invoked.
          task_id: Task identifier for logging.
          context: The prompt/context to send to the agent.
          sleep_fn: Injectable sleep function. Defaults to time.sleep.
                    In production, this is DBOS.sleep() for durable sleep.

      Returns:
          The result from invoke_fn.
      """
      if sleep_fn is None:
          sleep_fn = _default_sleep

      # Step 1: Check if we're already paused by another workflow
      pause_check = check_pause_before_invoke(db)
      if pause_check.paused and pause_check.resume_at is not None:
          wait_seconds = max(0, pause_check.resume_at - time.time())
          sleep_fn(wait_seconds)

      # Step 2: Try the invocation
      try:
          result = invoke_fn(role=role, task_id=task_id, context=context)
          return result
      except RateLimitError as e:
          # Step 3: Set global pause, sleep, clear, retry
          seconds = handle_rate_limit_error(db, e)
          sleep_fn(seconds)
          clear_global_pause(db)
          result = invoke_fn(role=role, task_id=task_id, context=context)
          return result
  ```

  **Run:** `pixi run pytest tests/concurrency/test_rate_limit_invoke.py -v`

  **Expect:** All tests pass.

---

## Task 7: CLI Commands — `devteam prioritize` and `--priority` Flag

**File:** `tests/concurrency/test_cli_priority.py`

### Steps

- [ ] **7a. Write failing tests for priority CLI commands**

  **File:** `tests/concurrency/test_cli_priority.py`

  ```python
  """Tests for priority-related CLI commands."""
  import sqlite3
  import pytest
  from unittest.mock import patch, MagicMock

  from devteam.concurrency.priority import Priority
  from devteam.concurrency.queue import (
      init_queue_table,
      enqueue_agent_invocation,
      dequeue_next,
  )
  from devteam.concurrency.cli_priority import (
      prioritize_task,
      parse_priority_flag,
  )


  @pytest.fixture
  def db(tmp_path):
      db_path = str(tmp_path / "test.sqlite")
      conn = sqlite3.connect(db_path)
      init_queue_table(conn)
      yield conn
      conn.close()


  class TestPrioritizeTask:
      def test_bump_task_to_high(self, db):
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-3",
              role="backend", priority=Priority.NORMAL,
          )
          result = prioritize_task(db, job_id="W-1", task_id="T-3", priority=Priority.HIGH)
          assert result.success is True
          assert result.new_priority == Priority.HIGH

      def test_prioritize_nonexistent_task(self, db):
          result = prioritize_task(db, job_id="W-1", task_id="T-99", priority=Priority.HIGH)
          assert result.success is False
          assert "not found" in result.message.lower()

      def test_prioritize_affects_dequeue_order(self, db):
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-1",
              role="backend", priority=Priority.NORMAL,
          )
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-2",
              role="frontend", priority=Priority.NORMAL,
          )
          # Bump T-2 to high
          prioritize_task(db, job_id="W-1", task_id="T-2", priority=Priority.HIGH)
          # T-2 should now dequeue first
          item = dequeue_next(db, max_concurrent=3)
          assert item is not None
          assert item.task_id == "T-2"


  class TestParsePriorityFlag:
      def test_parse_high(self):
          assert parse_priority_flag("high") == Priority.HIGH

      def test_parse_low(self):
          assert parse_priority_flag("low") == Priority.LOW

      def test_parse_none_returns_default(self):
          assert parse_priority_flag(None) == Priority.NORMAL

      def test_parse_invalid_raises(self):
          with pytest.raises(ValueError):
              parse_priority_flag("critical")
  ```

  **Run:** `pixi run pytest tests/concurrency/test_cli_priority.py -v`

  **Expect:** All tests fail (module not found).

- [ ] **7b. Implement CLI priority functions**

  **File:** `src/devteam/concurrency/cli_priority.py`

  ```python
  """CLI priority commands for devteam prioritize and --priority flag.

  These are the business logic functions called by the CLI layer.
  The actual Click/Typer command definitions live in the CLI module
  and delegate to these functions.
  """
  from __future__ import annotations

  import sqlite3
  from dataclasses import dataclass
  from typing import Optional

  from devteam.concurrency.priority import Priority
  from devteam.concurrency.queue import PENDING


  @dataclass
  class PrioritizeResult:
      """Result of a prioritize operation."""

      success: bool
      message: str
      new_priority: Optional[Priority] = None


  def prioritize_task(
      db: sqlite3.Connection,
      job_id: str,
      task_id: str,
      priority: Priority,
  ) -> PrioritizeResult:
      """Update the priority of a queued task.

      Only affects pending (not yet active) tasks.

      Args:
          db: SQLite connection.
          job_id: Job identifier (e.g., "W-1").
          task_id: Task identifier (e.g., "T-3").
          priority: New priority level.

      Returns:
          PrioritizeResult with success status and message.
      """
      # Check if the task exists and is pending
      row = db.execute(
          """
          SELECT id, status FROM agent_queue
          WHERE job_id = ? AND task_id = ? AND status = ?
          """,
          (job_id, task_id, PENDING),
      ).fetchone()

      if row is None:
          # Check if it exists at all
          exists = db.execute(
              "SELECT id, status FROM agent_queue WHERE job_id = ? AND task_id = ?",
              (job_id, task_id),
          ).fetchone()
          if exists is None:
              return PrioritizeResult(
                  success=False,
                  message=f"Task {job_id}/{task_id} not found in queue.",
              )
          return PrioritizeResult(
              success=False,
              message=(
                  f"Task {job_id}/{task_id} is {exists[1]}, "
                  f"can only prioritize pending tasks."
              ),
          )

      db.execute(
          "UPDATE agent_queue SET priority = ? WHERE id = ?",
          (priority.to_int(), row[0]),
      )
      db.commit()

      return PrioritizeResult(
          success=True,
          message=f"Task {job_id}/{task_id} priority set to {priority.name.lower()}.",
          new_priority=priority,
      )


  def parse_priority_flag(value: Optional[str]) -> Priority:
      """Parse the --priority CLI flag value.

      Returns Priority.NORMAL if value is None (not specified).

      Raises:
          ValueError: If value is not a valid priority string.
      """
      if value is None:
          return Priority.default()
      return Priority.from_string(value)
  ```

  **Run:** `pixi run pytest tests/concurrency/test_cli_priority.py -v`

  **Expect:** All tests pass.

---

## Task 8: Rate Limit Status Display

**File:** `tests/concurrency/test_status_display.py`

### Steps

- [ ] **8a. Write failing tests for rate limit status output**

  **File:** `tests/concurrency/test_status_display.py`

  ```python
  """Tests for rate limit status display in devteam status."""
  import sqlite3
  import time

  import pytest
  from devteam.concurrency.rate_limit import (
      init_pause_table,
      set_global_pause,
  )
  from devteam.concurrency.queue import (
      init_queue_table,
      enqueue_agent_invocation,
      dequeue_next,
      get_active_count,
  )
  from devteam.concurrency.priority import Priority
  from devteam.concurrency.status_display import (
      format_rate_limit_status,
      format_queue_status,
  )


  @pytest.fixture
  def db(tmp_path):
      db_path = str(tmp_path / "test.sqlite")
      conn = sqlite3.connect(db_path)
      init_pause_table(conn)
      init_queue_table(conn)
      yield conn
      conn.close()


  class TestFormatRateLimitStatus:
      def test_no_output_when_not_paused(self, db):
          """Rate limit line only shows when active — conditional display."""
          output = format_rate_limit_status(db)
          assert output is None

      def test_shows_remaining_time_when_paused(self, db):
          set_global_pause(db, seconds=6120)  # 1h 42m
          output = format_rate_limit_status(db)
          assert output is not None
          assert "Rate limited" in output
          assert "1h" in output

      def test_shows_minutes_only_when_under_hour(self, db):
          set_global_pause(db, seconds=300)  # 5 minutes
          output = format_rate_limit_status(db)
          assert output is not None
          assert "5m" in output

      def test_shows_seconds_when_under_minute(self, db):
          set_global_pause(db, seconds=45)
          output = format_rate_limit_status(db)
          assert output is not None
          assert "45s" in output or "44s" in output  # timing tolerance


  class TestFormatQueueStatus:
      def test_shows_active_and_max(self, db):
          enqueue_agent_invocation(
              db, job_id="W-1", task_id="T-1",
              role="backend", priority=Priority.NORMAL,
          )
          dequeue_next(db, max_concurrent=3)
          output = format_queue_status(db, max_concurrent=3)
          assert "1/3" in output

      def test_shows_zero_when_idle(self, db):
          output = format_queue_status(db, max_concurrent=3)
          assert "0/3" in output

      def test_includes_agents_running_label(self, db):
          output = format_queue_status(db, max_concurrent=3)
          assert "Agents running" in output
  ```

  **Run:** `pixi run pytest tests/concurrency/test_status_display.py -v`

  **Expect:** All tests fail (module not found).

- [ ] **8b. Implement status display functions**

  **File:** `src/devteam/concurrency/status_display.py`

  ```python
  """Status display formatting for rate limits and queue state.

  Used by `devteam status` to show rate limit state (conditional —
  only when a pause is active) and agent concurrency counts.
  """
  from __future__ import annotations

  import sqlite3
  from typing import Optional

  from devteam.concurrency.rate_limit import get_global_pause
  from devteam.concurrency.queue import get_active_count


  def _format_duration(seconds: float) -> str:
      """Format seconds into a human-readable duration string."""
      seconds = int(seconds)
      if seconds < 60:
          return f"{seconds}s"
      minutes = seconds // 60
      if minutes < 60:
          return f"{minutes}m"
      hours = minutes // 60
      remaining_minutes = minutes % 60
      if remaining_minutes == 0:
          return f"{hours}h"
      return f"{hours}h {remaining_minutes}m"


  def format_rate_limit_status(db: sqlite3.Connection) -> Optional[str]:
      """Format rate limit status for display.

      Returns None if not paused (conditional display — only shown when active).
      Returns a formatted string like "Rate limited — resumes in 1h 42m" when paused.
      """
      pause = get_global_pause(db)
      if pause is None:
          return None
      remaining = pause.remaining_seconds()
      duration = _format_duration(remaining)
      return f"Rate limited — resumes in {duration}"


  def format_queue_status(db: sqlite3.Connection, max_concurrent: int) -> str:
      """Format queue/concurrency status for display.

      Always shown: "Agents running: N/M"
      """
      active = get_active_count(db)
      return f"Agents running: {active}/{max_concurrent}"
  ```

  **Run:** `pixi run pytest tests/concurrency/test_status_display.py -v`

  **Expect:** All tests pass.

---

## Task 9: Concurrency Configuration Loading

**File:** `tests/concurrency/test_config.py`

### Steps

- [ ] **9a. Write failing tests for config.toml concurrency settings**

  **File:** `tests/concurrency/test_config.py`

  ```python
  """Tests for loading concurrency configuration from config.toml."""
  import pytest
  from devteam.concurrency.config import (
      load_concurrency_config,
      ConcurrencyConfig,
  )


  class TestConcurrencyConfig:
      def test_load_from_full_config(self):
          config = {
              "general": {"max_concurrent_agents": 5},
              "rate_limit": {"default_backoff_seconds": 900},
          }
          cc = load_concurrency_config(config)
          assert cc.max_concurrent_agents == 5
          assert cc.default_backoff_seconds == 900

      def test_defaults_when_missing(self):
          cc = load_concurrency_config({})
          assert cc.max_concurrent_agents == 3
          assert cc.default_backoff_seconds == 1800

      def test_partial_config(self):
          config = {"general": {"max_concurrent_agents": 10}}
          cc = load_concurrency_config(config)
          assert cc.max_concurrent_agents == 10
          assert cc.default_backoff_seconds == 1800  # default

      def test_invalid_concurrency_raises(self):
          config = {"general": {"max_concurrent_agents": -1}}
          with pytest.raises(ValueError, match="must be positive"):
              load_concurrency_config(config)

      def test_invalid_backoff_raises(self):
          config = {"rate_limit": {"default_backoff_seconds": 0}}
          with pytest.raises(ValueError, match="must be positive"):
              load_concurrency_config(config)
  ```

  **Run:** `pixi run pytest tests/concurrency/test_config.py -v`

  **Expect:** All tests fail (module not found).

- [ ] **9b. Implement concurrency config loading**

  **File:** `src/devteam/concurrency/config.py`

  ```python
  """Load concurrency-related configuration from config.toml.

  Reads [general].max_concurrent_agents and [rate_limit].default_backoff_seconds.
  """
  from __future__ import annotations

  from dataclasses import dataclass
  from typing import Any


  @dataclass
  class ConcurrencyConfig:
      """Concurrency and rate limit configuration."""

      max_concurrent_agents: int
      default_backoff_seconds: int


  def load_concurrency_config(config: dict[str, Any]) -> ConcurrencyConfig:
      """Load concurrency config from a parsed config.toml dict.

      Args:
          config: Parsed TOML configuration dictionary.

      Returns:
          ConcurrencyConfig with validated values.

      Raises:
          ValueError: If any value is invalid.
      """
      general = config.get("general", {})
      rate_limit = config.get("rate_limit", {})

      max_concurrent = general.get("max_concurrent_agents", 3)
      backoff = rate_limit.get("default_backoff_seconds", 1800)

      if max_concurrent <= 0:
          raise ValueError("max_concurrent_agents must be positive")
      if backoff <= 0:
          raise ValueError("default_backoff_seconds must be positive")

      return ConcurrencyConfig(
          max_concurrent_agents=max_concurrent,
          default_backoff_seconds=backoff,
      )
  ```

  **Run:** `pixi run pytest tests/concurrency/test_config.py -v`

  **Expect:** All tests pass.

---

## Task 10: Integration Test — Full Queue + Rate Limit + Priority Flow

**File:** `tests/concurrency/test_integration.py`

### Steps

- [ ] **10a. Write integration test that exercises the full concurrency stack**

  **File:** `tests/concurrency/test_integration.py`

  ```python
  """Integration test: full concurrency stack.

  Exercises queue + rate limit + priority + approval gates together.
  Simulates a multi-job scenario with rate limit interruption.
  """
  import sqlite3

  import pytest
  from devteam.concurrency.priority import Priority
  from devteam.concurrency.queue import (
      init_queue_table,
      enqueue_agent_invocation,
      dequeue_next,
      get_queue_depth,
      get_active_count,
  )
  from devteam.concurrency.rate_limit import (
      init_pause_table,
      is_paused,
  )
  from devteam.concurrency.invoke import (
      rate_limit_aware_invoke,
      RateLimitError,
  )
  from devteam.concurrency.approval import (
      load_approval_gates,
      check_approval,
  )
  from devteam.concurrency.config import load_concurrency_config
  from devteam.concurrency.status_display import (
      format_rate_limit_status,
      format_queue_status,
  )
  from devteam.concurrency.cli_priority import prioritize_task


  @pytest.fixture
  def db(tmp_path):
      db_path = str(tmp_path / "integration.sqlite")
      conn = sqlite3.connect(db_path)
      init_pause_table(conn)
      init_queue_table(conn)
      yield conn
      conn.close()


  class TestFullConcurrencyStack:
      def test_multi_job_priority_queue_flow(self, db):
          """Two jobs enqueue tasks. High-priority job dequeues first."""
          # Job W-1: normal priority
          enqueue_agent_invocation(
              db, "W-1", "T-1", "backend", Priority.NORMAL
          )
          enqueue_agent_invocation(
              db, "W-1", "T-2", "frontend", Priority.NORMAL
          )
          # Job W-2: high priority
          enqueue_agent_invocation(
              db, "W-2", "T-1", "data", Priority.HIGH
          )

          assert get_queue_depth(db) == 3

          # Dequeue should return W-2/T-1 first (high priority)
          item1 = dequeue_next(db, max_concurrent=3)
          assert item1 is not None
          assert item1.job_id == "W-2"
          assert item1.task_id == "T-1"

          # Then W-1/T-1 (normal, FIFO)
          item2 = dequeue_next(db, max_concurrent=3)
          assert item2 is not None
          assert item2.job_id == "W-1"
          assert item2.task_id == "T-1"

      def test_rate_limit_pauses_all_jobs(self, db):
          """Rate limit on one invocation pauses the global system."""
          call_count = 0

          def mock_invoke(**kwargs):
              nonlocal call_count
              call_count += 1
              if call_count == 1:
                  raise RateLimitError(
                      "Rate limit exceeded. Retry after 60 seconds."
                  )
              return {"status": "completed"}

          sleep_calls = []

          def mock_sleep(seconds):
              sleep_calls.append(seconds)

          result = rate_limit_aware_invoke(
              db=db,
              invoke_fn=mock_invoke,
              role="backend",
              task_id="T-1",
              context="test",
              sleep_fn=mock_sleep,
          )

          assert result == {"status": "completed"}
          assert len(sleep_calls) == 1
          assert sleep_calls[0] == 60
          # After retry, pause should be cleared
          assert is_paused(db) is False

      def test_prioritize_changes_dequeue_order(self, db):
          """devteam prioritize bumps a task ahead in queue."""
          enqueue_agent_invocation(
              db, "W-1", "T-1", "backend", Priority.NORMAL
          )
          enqueue_agent_invocation(
              db, "W-1", "T-2", "frontend", Priority.NORMAL
          )

          # Bump T-2 to high
          result = prioritize_task(db, "W-1", "T-2", Priority.HIGH)
          assert result.success is True

          # T-2 should dequeue first now
          item = dequeue_next(db, max_concurrent=3)
          assert item is not None
          assert item.task_id == "T-2"

      def test_approval_gates_with_manual_merge(self):
          """Manual merge config blocks automatic merge."""
          config = {
              "approval": {
                  "commit": "auto",
                  "push": "auto",
                  "open_pr": "auto",
                  "merge": "manual",
                  "cleanup": "auto",
              }
          }
          gates = load_approval_gates(config)
          # Commit proceeds
          assert check_approval(gates, "commit").approved is True
          # Merge requires human
          merge_decision = check_approval(gates, "merge")
          assert merge_decision.approved is False
          assert merge_decision.needs_human is True
          # push_to_main always blocked
          assert check_approval(gates, "push_to_main").blocked is True

      def test_config_drives_queue_concurrency(self, db):
          """Config max_concurrent_agents limits simultaneous agents."""
          config = {"general": {"max_concurrent_agents": 2}}
          cc = load_concurrency_config(config)

          enqueue_agent_invocation(db, "W-1", "T-1", "a", Priority.NORMAL)
          enqueue_agent_invocation(db, "W-1", "T-2", "b", Priority.NORMAL)
          enqueue_agent_invocation(db, "W-1", "T-3", "c", Priority.NORMAL)

          item1 = dequeue_next(db, cc.max_concurrent_agents)
          item2 = dequeue_next(db, cc.max_concurrent_agents)
          item3 = dequeue_next(db, cc.max_concurrent_agents)

          assert item1 is not None
          assert item2 is not None
          assert item3 is None  # blocked by concurrency limit

      def test_status_display_integration(self, db):
          """Status display reflects live queue and rate limit state."""
          # No pause active
          assert format_rate_limit_status(db) is None

          # Queue status shows 0/3
          output = format_queue_status(db, max_concurrent=3)
          assert "0/3" in output

          # Enqueue and dequeue
          enqueue_agent_invocation(
              db, "W-1", "T-1", "backend", Priority.NORMAL
          )
          dequeue_next(db, max_concurrent=3)
          output = format_queue_status(db, max_concurrent=3)
          assert "1/3" in output
  ```

  **Run:** `pixi run pytest tests/concurrency/test_integration.py -v`

  **Expect:** All tests pass.

---

## Task 11: Test `__init__.py` and Create `tests/concurrency/__init__.py`

### Steps

- [ ] **11a. Create test package init and verify full test suite passes**

  **File:** `tests/concurrency/__init__.py`

  ```python
  """Concurrency module tests."""
  ```

  **Run:** `pixi run pytest tests/concurrency/ -v --tb=short`

  **Expect:** All tests across all test files pass. Summary shows 60+ tests passing.

---

## Summary

| Task | Module | Tests | Key Verification |
|------|--------|-------|------------------|
| 1 | `priority.py` | 12 | Enum ordering, string parsing, task sorting |
| 2 | `rate_limit.py` | 12 | Pause flag CRUD, expiry, error parsing |
| 3 | `queue.py` | 12 | Enqueue/dequeue, concurrency limit, multi-job |
| 4 | `approval.py` | 11 | Policy parsing, gate checking, push_to_main forced never |
| 5 | `test_durable_sleep.py` | 5 | Pause persists across connection close/reopen |
| 6 | `invoke.py` | 6 | Rate-limit-aware invocation, mock Agent SDK |
| 7 | `cli_priority.py` | 7 | Prioritize command, --priority flag |
| 8 | `status_display.py` | 6 | Conditional rate limit display, queue counts |
| 9 | `config.py` | 5 | Config loading, defaults, validation |
| 10 | Integration | 6 | Full stack: queue + rate limit + priority + approval |
| 11 | Suite run | All | All tests pass together |

**Total:** ~82 tests across 10 test files covering the complete concurrency subsystem.

**Dependencies on Plan 1 + 2:** This plan assumes the `src/devteam/` package exists with `__init__.py` (Plan 1: project scaffold) and that SQLite is initialized by the DBOS setup (Plan 2: daemon + workflow engine). The queue and pause tables defined here will be created alongside the DBOS-managed tables.

**DBOS Integration Note:** The queue implementation uses raw SQLite for testability. In production integration (after Plan 2), `enqueue_agent_invocation` wraps `DBOS.Queue.enqueue()` and `sleep_fn` maps to `DBOS.sleep()` for durable sleep that survives crashes. The SQLite-backed implementation here has identical semantics and the same table schema, making the swap mechanical.
