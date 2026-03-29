"""Tests for interactive command parsing and validation."""

from __future__ import annotations

from devteam.cli.interactive import (
    ParsedCommand,
    format_help,
    parse_command,
    validate_command,
)


class TestParseCommand:
    def test_answer_command(self) -> None:
        cmd = parse_command("/answer Q-1 Use JWT")
        assert cmd is not None
        assert cmd.name == "answer"
        assert cmd.args == ["Q-1", "Use JWT"]

    def test_answer_with_long_text(self) -> None:
        cmd = parse_command(
            "/answer Q-1 Use JWT because it's stateless and works with our API gateway"
        )
        assert cmd is not None
        assert cmd.args[0] == "Q-1"
        assert "stateless" in cmd.args[1]

    def test_comment_command(self) -> None:
        cmd = parse_command("/comment T-3 Please add error handling")
        assert cmd is not None
        assert cmd.name == "comment"
        assert cmd.args == ["T-3", "Please add error handling"]

    def test_pause_command(self) -> None:
        cmd = parse_command("/pause")
        assert cmd is not None
        assert cmd.name == "pause"
        assert cmd.args == []

    def test_resume_command(self) -> None:
        cmd = parse_command("/resume")
        assert cmd is not None
        assert cmd.name == "resume"
        assert cmd.args == []

    def test_cancel_command(self) -> None:
        cmd = parse_command("/cancel")
        assert cmd is not None
        assert cmd.name == "cancel"
        assert cmd.args == []

    def test_status_command(self) -> None:
        cmd = parse_command("/status")
        assert cmd is not None
        assert cmd.name == "status"
        assert cmd.args == []

    def test_verbose_command(self) -> None:
        cmd = parse_command("/verbose T-1")
        assert cmd is not None
        assert cmd.name == "verbose"
        assert cmd.args == ["T-1"]

    def test_quiet_command(self) -> None:
        cmd = parse_command("/quiet T-1")
        assert cmd is not None
        assert cmd.name == "quiet"
        assert cmd.args == ["T-1"]

    def test_priority_command(self) -> None:
        cmd = parse_command("/priority T-3 high")
        assert cmd is not None
        assert cmd.name == "priority"
        assert cmd.args == ["T-3", "high"]

    def test_help_command(self) -> None:
        cmd = parse_command("/help")
        assert cmd is not None
        assert cmd.name == "help"
        assert cmd.args == []

    def test_not_a_command(self) -> None:
        assert parse_command("hello world") is None

    def test_empty_string(self) -> None:
        assert parse_command("") is None

    def test_just_slash(self) -> None:
        assert parse_command("/") is None

    def test_unknown_command(self) -> None:
        assert parse_command("/foobar") is None

    def test_case_insensitive(self) -> None:
        cmd = parse_command("/PAUSE")
        assert cmd is not None
        assert cmd.name == "pause"

    def test_leading_whitespace(self) -> None:
        cmd = parse_command("  /status")
        assert cmd is not None
        assert cmd.name == "status"

    def test_preserves_raw(self) -> None:
        cmd = parse_command("  /answer Q-1 Use JWT  ")
        assert cmd is not None
        assert cmd.raw == "/answer Q-1 Use JWT"


class TestValidateCommand:
    def test_answer_missing_args(self) -> None:
        cmd = ParsedCommand(name="answer", args=["Q-1"], raw="/answer Q-1")
        error = validate_command(cmd)
        assert error is not None
        assert "Usage" in error

    def test_answer_valid(self) -> None:
        cmd = ParsedCommand(name="answer", args=["Q-1", "Use JWT"], raw="/answer Q-1 Use JWT")
        assert validate_command(cmd) is None

    def test_comment_missing_args(self) -> None:
        cmd = ParsedCommand(name="comment", args=["T-3"], raw="/comment T-3")
        error = validate_command(cmd)
        assert error is not None

    def test_verbose_missing_target(self) -> None:
        cmd = ParsedCommand(name="verbose", args=[], raw="/verbose")
        error = validate_command(cmd)
        assert error is not None

    def test_pause_no_args_needed(self) -> None:
        cmd = ParsedCommand(name="pause", args=[], raw="/pause")
        assert validate_command(cmd) is None

    def test_priority_missing_level(self) -> None:
        cmd = ParsedCommand(name="priority", args=["T-3"], raw="/priority T-3")
        error = validate_command(cmd)
        assert error is not None


class TestFormatHelp:
    def test_help_contains_all_commands(self) -> None:
        help_text = format_help()
        assert "/answer" in help_text
        assert "/comment" in help_text
        assert "/pause" in help_text
        assert "/resume" in help_text
        assert "/cancel" in help_text
        assert "/status" in help_text
        assert "/verbose" in help_text
        assert "/quiet" in help_text
        assert "/priority" in help_text
        assert "/help" in help_text
