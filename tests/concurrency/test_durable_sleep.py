"""Tests for durable sleep behavior across simulated restart.

Verifies that the global pause flag persists in SQLite and survives
a simulated process restart (close + reopen connection).
"""

import sqlite3

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
