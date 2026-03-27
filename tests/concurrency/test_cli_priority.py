"""Tests for priority-related CLI commands."""

import sqlite3

import pytest

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
    from devteam.concurrency.rate_limit import init_pause_table

    init_pause_table(conn)
    yield conn
    conn.close()


class TestPrioritizeTask:
    def test_bump_task_to_high(self, db):
        enqueue_agent_invocation(
            db,
            job_id="W-1",
            task_id="T-3",
            role="backend",
            priority=Priority.NORMAL,
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
            db,
            job_id="W-1",
            task_id="T-1",
            role="backend",
            priority=Priority.NORMAL,
        )
        enqueue_agent_invocation(
            db,
            job_id="W-1",
            task_id="T-2",
            role="frontend",
            priority=Priority.NORMAL,
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
