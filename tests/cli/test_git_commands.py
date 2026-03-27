"""Tests for git-related CLI commands."""

from unittest.mock import patch

from typer.testing import CliRunner

from devteam.cli.commands.git_commands import git_app


runner = CliRunner()


class TestCancelCommand:
    def test_cancel_job(self):
        """devteam cancel W-1 triggers full cleanup."""
        with patch("devteam.cli.commands.git_commands.send_cancel_request") as mock:
            mock.return_value = {
                "success": True,
                "cleaned": [
                    {"action": "pr_closed", "branch": "feat/a", "pr_number": 12},
                ],
                "preserved": [],
            }
            result = runner.invoke(git_app, ["cancel", "W-1"])
            assert result.exit_code == 0
            assert "CANCELLED" in result.output or "Cancelled" in result.output

    def test_cancel_nonexistent_job(self):
        """Cancel on a nonexistent job shows an error."""
        with patch("devteam.cli.commands.git_commands.send_cancel_request") as mock:
            mock.return_value = {"success": False, "error": "Job W-99 not found"}
            result = runner.invoke(git_app, ["cancel", "W-99"])
            assert result.exit_code == 1
            assert "not found" in result.output.lower()

    def test_cancel_threads_revert_merged(self):
        """--revert-merged flag is threaded through to send_cancel_request."""
        with patch("devteam.cli.commands.git_commands.send_cancel_request") as mock:
            mock.return_value = {"success": True, "cleaned": [], "preserved": []}
            result = runner.invoke(git_app, ["cancel", "W-1", "--revert-merged"])
            assert result.exit_code == 0
            mock.assert_called_once_with("W-1", revert_merged=True)


class TestMergeCommand:
    def test_merge_pr(self):
        """devteam merge triggers merge with check verification."""
        with patch("devteam.cli.commands.git_commands.send_merge_request") as mock:
            mock.return_value = {"success": True, "pr_number": 42, "merged": True}
            result = runner.invoke(git_app, ["merge", "W-1/PR-42"])
            assert result.exit_code == 0

    def test_merge_failing_pr(self):
        """Refuses to merge a PR with failing checks."""
        with patch("devteam.cli.commands.git_commands.send_merge_request") as mock:
            mock.return_value = {
                "success": False,
                "error": "CI checks not passed",
                "failed_checks": ["lint", "test"],
            }
            result = runner.invoke(git_app, ["merge", "W-1/PR-42"])
            assert result.exit_code == 1
            assert "not passed" in result.output.lower()


class TestTakeoverCommand:
    def test_takeover_shows_worktree(self):
        """devteam takeover outputs worktree path."""
        with patch("devteam.cli.commands.git_commands.send_takeover_request") as mock:
            mock.return_value = {
                "success": True,
                "worktree_path": "/repo/.worktrees/feat-auth",
                "task_id": "T-3",
            }
            result = runner.invoke(git_app, ["takeover", "W-1/T-3"])
            assert result.exit_code == 0
            assert ".worktrees/feat-auth" in result.output

    def test_takeover_shows_correct_handback_command(self):
        """Takeover output references 'devteam git handback', not 'devteam handback'."""
        with patch("devteam.cli.commands.git_commands.send_takeover_request") as mock:
            mock.return_value = {
                "success": True,
                "worktree_path": "/repo/.worktrees/feat-auth",
                "task_id": "T-3",
            }
            result = runner.invoke(git_app, ["takeover", "W-1/T-3"])
            assert result.exit_code == 0
            assert "devteam git handback W-1/T-3" in result.output

    def test_takeover_failure(self):
        """devteam takeover shows error on failure."""
        with patch("devteam.cli.commands.git_commands.send_takeover_request") as mock:
            mock.return_value = {
                "success": False,
                "error": "Task W-1/T-3 is not in a pauseable state",
            }
            result = runner.invoke(git_app, ["takeover", "W-1/T-3"])
            assert result.exit_code == 1
            assert "not in a pauseable state" in result.output.lower()


class TestHandbackCommand:
    def test_handback_validates(self):
        """devteam handback runs validation before resuming."""
        with patch("devteam.cli.commands.git_commands.send_handback_request") as mock:
            mock.return_value = {
                "success": True,
                "validation": {"clean": True, "scope_ok": True},
            }
            result = runner.invoke(git_app, ["handback", "W-1/T-3"])
            assert result.exit_code == 0

    def test_handback_dirty_worktree(self):
        """Handback rejects dirty worktree."""
        with patch("devteam.cli.commands.git_commands.send_handback_request") as mock:
            mock.return_value = {
                "success": False,
                "error": "Worktree has uncommitted changes",
                "validation": {"clean": False},
            }
            result = runner.invoke(git_app, ["handback", "W-1/T-3"])
            assert result.exit_code == 1
            assert "uncommitted" in result.output.lower()
