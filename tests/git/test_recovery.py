"""Tests for idempotent recovery checks.

Pattern: before any side-effecting step, check if the effect
already happened. Every external action is idempotent on retry.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from devteam.git.recovery import (
    check_worktree_state,
    check_branch_pushed,
    check_pr_exists,
    check_pr_merged,
    reset_worktree_to_clean,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    readme = repo / "README.md"
    readme.write_text("# Test")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return repo


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
    def test_branch_on_remote(self, git_repo: Path):
        """Returns True if branch exists on remote with expected commits."""
        with patch("devteam.git.recovery.remote_branch_exists", return_value=True):
            assert check_branch_pushed(git_repo, "feat/x") is True

    def test_branch_not_on_remote(self, git_repo: Path):
        """Returns False if branch does not exist on remote."""
        with patch("devteam.git.recovery.remote_branch_exists", return_value=False):
            assert check_branch_pushed(git_repo, "feat/y") is False


class TestCheckPRExists:
    def test_pr_exists(self, tmp_path: Path):
        """Returns PR number if PR exists for branch."""
        with patch("devteam.git.recovery.find_existing_pr") as mock_find:
            from devteam.git.pr import PRInfo

            mock_find.return_value = PRInfo(number=42, url="...", branch="feat/x")
            result = check_pr_exists(tmp_path, "feat/x")
            assert result == 42

    def test_pr_does_not_exist(self, tmp_path: Path):
        """Returns None if no PR exists."""
        with patch("devteam.git.recovery.find_existing_pr", return_value=None):
            result = check_pr_exists(tmp_path, "feat/y")
            assert result is None


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
