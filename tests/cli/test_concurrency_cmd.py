"""Tests for concurrency-related CLI commands (prioritize)."""

from typer.testing import CliRunner

from devteam.cli.main import app

runner = CliRunner()


class TestPrioritizeCommand:
    def test_prioritize_exits_with_error(self):
        """prioritize should fail with exit code 1 (daemon not implemented)."""
        result = runner.invoke(app, ["prioritize", "W-1", "T-3", "high"])
        assert result.exit_code == 1
        assert "not yet implemented" in result.output

    def test_prioritize_invalid_level_still_fails(self):
        """Invalid priority level fails before reaching daemon check."""
        result = runner.invoke(app, ["prioritize", "W-1", "T-3", "critical"])
        assert result.exit_code == 1
