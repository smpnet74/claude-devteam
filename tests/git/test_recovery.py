"""Tests for idempotent recovery checks.

Pattern: before any side-effecting step, check if the effect
already happened. Every external action is idempotent on retry.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch


from devteam.git.recovery import (
    RecoveryCheck,
    check_worktree_state,
    check_branch_pushed,
    check_pr_exists,
    check_pr_merged,
    reset_worktree_to_clean,
)


class TestCheckWorktreeState:
    def test_clean_worktree(self, git_repo: Path):
        """Clean worktree returns CLEAN status."""
        check = check_worktree_state(git_repo)
        assert check.clean is True

    def test_dirty_worktree(self, git_repo: Path):
        """Worktree with uncommitted changes returns DIRTY."""
        (git_repo / "dirty.txt").write_text("uncommitted")
        check = check_worktree_state(git_repo)
        assert check.clean is False
        assert "dirty.txt" in check.details

    def test_nonexistent_path(self, tmp_path: Path):
        """Non-existent path returns appropriate status."""
        fake = tmp_path / "nope"
        check = check_worktree_state(fake)
        assert check.exists is False


class TestCheckBranchPushed:
    def test_branch_on_remote_matching(self, git_repo: Path):
        """Returns clean=True when local and remote SHAs match."""
        with patch("devteam.git.recovery.remote_branch_exists", return_value=True):
            with patch("devteam.git.recovery.git_run") as mock_git:
                mock_git.side_effect = ["abc12345", "abc12345"]
                result = check_branch_pushed(git_repo, "feat/x")
                assert isinstance(result, RecoveryCheck)
                assert result.exists is True
                assert result.clean is True
                assert "up to date" in result.details

    def test_branch_on_remote_diverged(self, git_repo: Path):
        """Returns clean=False when local and remote SHAs differ."""
        with patch("devteam.git.recovery.remote_branch_exists", return_value=True):
            with patch("devteam.git.recovery.git_run") as mock_git:
                mock_git.side_effect = ["abc12345", "def67890"]
                result = check_branch_pushed(git_repo, "feat/x")
                assert isinstance(result, RecoveryCheck)
                assert result.exists is True
                assert result.clean is False
                assert "diverged" in result.details

    def test_branch_not_on_remote(self, git_repo: Path):
        """Returns exists=False if branch does not exist on remote."""
        with patch("devteam.git.recovery.remote_branch_exists", return_value=False):
            result = check_branch_pushed(git_repo, "feat/y")
            assert isinstance(result, RecoveryCheck)
            assert result.exists is False
            assert result.clean is False

    def test_branch_compare_error(self, git_repo: Path):
        """Returns clean=False when rev-parse fails."""
        from devteam.git.helpers import GitError

        with patch("devteam.git.recovery.remote_branch_exists", return_value=True):
            with patch("devteam.git.recovery.git_run") as mock_git:
                mock_git.side_effect = GitError(["rev-parse"], 1, "not found")
                result = check_branch_pushed(git_repo, "feat/x")
                assert result.exists is True
                assert result.clean is False
                assert "Cannot compare" in result.details


class TestCheckPRExists:
    def test_pr_exists(self, tmp_path: Path):
        """Returns RecoveryCheck with exists=True if PR exists for branch."""
        with patch("devteam.git.recovery.find_existing_pr") as mock_find:
            from devteam.git.pr import PRInfo

            mock_find.return_value = PRInfo(number=42, url="...", branch="feat/x")
            result = check_pr_exists(tmp_path, "feat/x")
            assert isinstance(result, RecoveryCheck)
            assert result.exists is True
            assert "PR #42" in result.details

    def test_pr_does_not_exist(self, tmp_path: Path):
        """Returns RecoveryCheck with exists=False if no PR exists."""
        with patch("devteam.git.recovery.find_existing_pr", return_value=None):
            result = check_pr_exists(tmp_path, "feat/y")
            assert isinstance(result, RecoveryCheck)
            assert result.exists is False
            assert "No PR found" in result.details

    def test_pr_found_in_upstream(self, tmp_path: Path):
        """Returns exists=True when PR found in upstream repo (fork workflow)."""
        with patch("devteam.git.recovery.find_existing_pr", return_value=None):
            with patch("devteam.git.recovery.gh_run") as mock_gh:
                mock_gh.return_value = [{"number": 77, "url": "..."}]
                result = check_pr_exists(tmp_path, "feat/fork-fix", upstream_repo="org/upstream")
                assert result.exists is True
                assert "Upstream PR found" in result.details

    def test_pr_not_found_in_upstream(self, tmp_path: Path):
        """Returns exists=False when no PR in local or upstream."""
        with patch("devteam.git.recovery.find_existing_pr", return_value=None):
            with patch("devteam.git.recovery.gh_run") as mock_gh:
                mock_gh.return_value = []
                result = check_pr_exists(tmp_path, "feat/fork-fix", upstream_repo="org/upstream")
                assert result.exists is False


class TestCheckPRMerged:
    def test_pr_is_merged(self, tmp_path: Path):
        """Returns True if PR is already merged."""
        with patch("devteam.git.recovery.gh_run") as mock_gh:
            mock_gh.return_value = {"state": "MERGED"}
            assert check_pr_merged(tmp_path, 42) is True

    def test_pr_not_merged(self, tmp_path: Path):
        """Returns False if PR is still open."""
        with patch("devteam.git.recovery.gh_run") as mock_gh:
            mock_gh.return_value = {"state": "OPEN"}
            assert check_pr_merged(tmp_path, 42) is False


class TestResetWorktreeToClean:
    def test_reset_discards_changes(self, git_repo: Path):
        """Reset brings worktree back to last commit."""
        (git_repo / "dirty.txt").write_text("uncommitted")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "."],
            check=True,
            capture_output=True,
        )
        reset_worktree_to_clean(git_repo)
        check = check_worktree_state(git_repo)
        assert check.clean is True

    def test_reset_idempotent(self, git_repo: Path):
        """Reset on a clean worktree is a no-op."""
        reset_worktree_to_clean(git_repo)
        check = check_worktree_state(git_repo)
        assert check.clean is True
