"""Tests for rate-limit-aware agent invocation.

Mocks the Agent SDK to simulate RateLimitError and verifies the
orchestrator correctly sets the global pause, waits, clears, and retries.
"""

import sqlite3
from unittest.mock import MagicMock

import pytest
from devteam.concurrency.rate_limit import (
    init_pause_table,
    is_paused,
    get_global_pause,
    set_global_pause,
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
        mock_invoke = MagicMock(side_effect=[error, {"status": "completed"}])
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
        mock_invoke = MagicMock(side_effect=[error, {"status": "completed"}])
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
        mock_invoke = MagicMock(side_effect=[error, {"status": "completed"}])
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
