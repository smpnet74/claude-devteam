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
