"""Tests for rate limit status display in devteam status."""

import sqlite3

import pytest
from devteam.concurrency.rate_limit import (
    init_pause_table,
    set_global_pause,
)
from devteam.concurrency.queue import (
    init_queue_table,
    enqueue_agent_invocation,
    dequeue_next,
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
        """Rate limit line only shows when active -- conditional display."""
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
        assert "5m" in output or "4m" in output  # timing tolerance

    def test_shows_seconds_when_under_minute(self, db):
        set_global_pause(db, seconds=45)
        output = format_rate_limit_status(db)
        assert output is not None
        assert "45s" in output or "44s" in output  # timing tolerance


class TestFormatQueueStatus:
    def test_shows_active_and_max(self, db):
        enqueue_agent_invocation(
            db,
            job_id="W-1",
            task_id="T-1",
            role="backend",
            priority=Priority.NORMAL,
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
