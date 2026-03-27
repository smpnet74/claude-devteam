"""Tests for durable sleep behavior across simulated restart.

Verifies that the global pause flag persists in SQLite and survives
a simulated process restart (close + reopen connection).
Also tests the durable_sleep(), check_pending_sleep(), and cancel_sleep()
functions directly.
"""

import sqlite3

import pytest
from devteam.concurrency.durable_sleep import (
    cancel_sleep,
    check_pending_sleep,
    durable_sleep,
    resume_sleep,
    PendingSleep,
)
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


class TestDurableSleepFunction:
    """Tests for the durable_sleep(), check_pending_sleep(), and cancel_sleep() helpers."""

    @pytest.fixture
    def conn(self, tmp_path):
        """Return a fresh connection with the pause table initialized."""
        c = sqlite3.connect(str(tmp_path / "durable_func_test.sqlite"))
        init_pause_table(c)
        yield c
        c.close()

    def test_durable_sleep_sets_pause_calls_sleep_clears(self, conn):
        """durable_sleep sets the pause flag, calls sleep_fn, then clears it."""
        calls: list[float] = []
        durable_sleep(conn, duration_seconds=42.0, sleep_fn=calls.append)

        # sleep_fn was called with the duration
        assert calls == [42.0]
        # pause flag is cleared after sleep
        assert get_global_pause(conn) is None

    def test_check_pending_sleep_finds_active_pause(self, conn):
        """check_pending_sleep returns PendingSleep when a pause record exists."""
        set_global_pause(conn, seconds=600)
        pending = check_pending_sleep(conn)
        assert pending is not None
        assert pending.remaining_seconds() > 500
        assert pending.reason == "rate_limit"

    def test_check_pending_sleep_returns_none_when_clear(self, conn):
        """check_pending_sleep returns None when no pause exists."""
        assert check_pending_sleep(conn) is None

    def test_cancel_sleep_clears_pending(self, conn):
        """cancel_sleep removes the pause record so check_pending_sleep returns None."""
        set_global_pause(conn, seconds=600)
        assert is_paused(conn) is True
        cancel_sleep(conn)
        assert check_pending_sleep(conn) is None

    def test_durable_sleep_clears_pause_on_exception(self, conn):
        """Pause flag is cleared even when sleep_fn raises an exception."""

        def exploding_sleep(duration: float) -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            durable_sleep(conn, duration_seconds=10.0, sleep_fn=exploding_sleep)

        # pause must be cleared despite the exception
        assert get_global_pause(conn) is None

    def test_durable_sleep_does_not_clear_newer_pause(self, conn):
        """Workflow A's finally must not wipe a longer pause set by workflow B during A's sleep."""

        def sleep_and_set_longer_pause(duration: float) -> None:
            # Simulate workflow B setting a longer pause while A sleeps
            set_global_pause(conn, seconds=9999, reason="rate_limit_b")

        # Workflow A sleeps for 10s; during that sleep, B sets a 9999s pause
        durable_sleep(conn, duration_seconds=10.0, sleep_fn=sleep_and_set_longer_pause)

        # B's pause must still be active
        assert is_paused(conn) is True
        pause = get_global_pause(conn)
        assert pause is not None
        assert pause.remaining_seconds() > 9000

    def test_resume_sleep_does_not_clear_newer_pause(self, conn):
        """resume_sleep's finally must not wipe a longer pause set by another workflow."""
        set_global_pause(conn, seconds=60, reason="original")
        pending = PendingSleep(resume_at=get_global_pause(conn).resume_at, reason="original")

        def sleep_and_set_longer_pause(duration: float) -> None:
            set_global_pause(conn, seconds=9999, reason="rate_limit_b")

        resume_sleep(conn, pending, sleep_fn=sleep_and_set_longer_pause)

        assert is_paused(conn) is True
        pause = get_global_pause(conn)
        assert pause is not None
        assert pause.remaining_seconds() > 9000
