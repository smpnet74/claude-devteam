"""Tests for priority-related CLI commands."""

import pytest

from devteam.concurrency.cli_priority import parse_priority_flag
from devteam.concurrency.priority import Priority


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
