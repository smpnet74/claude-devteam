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
        enqueue_agent_invocation(db, "W-1", "T-1", "backend", Priority.NORMAL)
        enqueue_agent_invocation(db, "W-1", "T-2", "frontend", Priority.NORMAL)
        # Job W-2: high priority
        enqueue_agent_invocation(db, "W-2", "T-1", "data", Priority.HIGH)

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
                raise RateLimitError("Rate limit exceeded. Retry after 60 seconds.")
            return {"status": "completed"}

        sleep_calls: list[float] = []

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
        enqueue_agent_invocation(db, "W-1", "T-1", "backend", Priority.NORMAL)
        enqueue_agent_invocation(db, "W-1", "T-2", "frontend", Priority.NORMAL)

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
        enqueue_agent_invocation(db, "W-1", "T-1", "backend", Priority.NORMAL)
        dequeue_next(db, max_concurrent=3)
        output = format_queue_status(db, max_concurrent=3)
        assert "1/3" in output
