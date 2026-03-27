"""Tests for DBOS queue setup and enqueue logic."""

import sqlite3

import pytest
from devteam.concurrency.priority import Priority
from devteam.concurrency.queue import (
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
            priority=Priority.HIGH,
        )
        item = dequeue_next(db, max_concurrent=3)
        assert item is not None
        assert item.task_id == "T-2"
        assert item.priority == Priority.HIGH

    def test_dequeue_fifo_within_priority(self, db):
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
                db,
                job_id="W-1",
                task_id=f"T-{i}",
                role="backend",
                priority=Priority.NORMAL,
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
            db,
            job_id="W-1",
            task_id="T-1",
            role="backend",
            priority=Priority.NORMAL,
        )
        item = dequeue_next(db, max_concurrent=1)
        assert item is not None
        # Mark as complete
        item.mark_complete(db)
        # Now another item can be dequeued
        enqueue_agent_invocation(
            db,
            job_id="W-1",
            task_id="T-2",
            role="frontend",
            priority=Priority.NORMAL,
        )
        item2 = dequeue_next(db, max_concurrent=1)
        assert item2 is not None
        assert item2.task_id == "T-2"


class TestQueueDepthAndActive:
    def test_queue_depth_counts_pending(self, db):
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
        assert get_queue_depth(db) == 2

    def test_active_count_tracks_running(self, db):
        enqueue_agent_invocation(
            db,
            job_id="W-1",
            task_id="T-1",
            role="backend",
            priority=Priority.NORMAL,
        )
        assert get_active_count(db) == 0
        item = dequeue_next(db, max_concurrent=3)
        assert item is not None
        assert get_active_count(db) == 1

    def test_active_count_decreases_on_complete(self, db):
        enqueue_agent_invocation(
            db,
            job_id="W-1",
            task_id="T-1",
            role="backend",
            priority=Priority.NORMAL,
        )
        item = dequeue_next(db, max_concurrent=3)
        assert item is not None
        assert get_active_count(db) == 1
        item.mark_complete(db)
        assert get_active_count(db) == 0


class TestMultiJobFairness:
    def test_different_jobs_share_queue(self, db):
        enqueue_agent_invocation(
            db,
            job_id="W-1",
            task_id="T-1",
            role="backend",
            priority=Priority.NORMAL,
        )
        enqueue_agent_invocation(
            db,
            job_id="W-2",
            task_id="T-1",
            role="frontend",
            priority=Priority.NORMAL,
        )
        assert get_queue_depth(db) == 2

    def test_high_priority_job_tasks_dequeued_first(self, db):
        enqueue_agent_invocation(
            db,
            job_id="W-1",
            task_id="T-1",
            role="backend",
            priority=Priority.NORMAL,
        )
        enqueue_agent_invocation(
            db,
            job_id="W-2",
            task_id="T-1",
            role="frontend",
            priority=Priority.HIGH,
        )
        item = dequeue_next(db, max_concurrent=3)
        assert item is not None
        assert item.job_id == "W-2"
