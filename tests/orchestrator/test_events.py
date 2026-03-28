"""Tests for workflow event types and formatters."""

from devteam.orchestrator.events import EventLevel, LogEvent, format_log_event, make_log_key


class TestLogEvent:
    def test_create(self):
        evt = LogEvent(message="Task started", level=EventLevel.INFO, seq=1)
        assert evt.message == "Task started"
        assert evt.seq == 1

    def test_format_job(self):
        evt = LogEvent(message="Routing... full_project", level=EventLevel.INFO, seq=1)
        assert "[W-1]" in format_log_event(evt, job_id="W-1")

    def test_format_task(self):
        evt = LogEvent(message="starting", level=EventLevel.INFO, seq=1)
        assert "[W-1/T-1]" in format_log_event(evt, job_id="W-1", task_id="T-1")

    def test_format_question(self):
        evt = LogEvent(message="Redis or JWT?", level=EventLevel.QUESTION, seq=1)
        assert "QUESTION" in format_log_event(evt, job_id="W-1", task_id="T-2")

    def test_format_error(self):
        evt = LogEvent(message="Agent failed", level=EventLevel.ERROR, seq=1)
        assert "ERROR" in format_log_event(evt, job_id="W-1")

    def test_format_warn(self):
        evt = LogEvent(message="Slow response", level=EventLevel.WARN, seq=1)
        assert "WARN" in format_log_event(evt, job_id="W-1")

    def test_format_success(self):
        evt = LogEvent(message="Complete", level=EventLevel.SUCCESS, seq=1)
        line = format_log_event(evt, job_id="W-1")
        assert "[W-1]" in line
        assert "Complete" in line

    def test_timestamp_auto_set(self):
        evt = LogEvent(message="test", level=EventLevel.INFO, seq=1)
        assert evt.timestamp > 0


class TestMakeLogKey:
    def test_padding(self):
        assert make_log_key(1) == "log:000001"
        assert make_log_key(999999) == "log:999999"

    def test_sequential(self):
        assert make_log_key(42) == "log:000042"
        assert make_log_key(100) == "log:000100"
