"""Tests for rate limit detection and global pause flag."""

import sqlite3
import time

import pytest
from devteam.concurrency.rate_limit import (
    init_pause_table,
    set_global_pause,
    get_global_pause,
    clear_global_pause,
    is_paused,
    check_pause_before_invoke,
    handle_rate_limit_error,
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
        error = Exception("anthropic.RateLimitError: retry-after: 120")
        seconds = handle_rate_limit_error(db, error)
        assert seconds == 120
