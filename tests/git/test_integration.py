"""Integration test: full cancel cleanup flow using real git repos.

Tests the complete lifecycle: create worktrees + branches, then cancel
with full cleanup, verify everything is removed.
"""

from pathlib import Path
from unittest.mock import patch

from devteam.git import (
    create_worktree,
    branch_exists,
    worktree_exists,
    cleanup_on_cancel,
    check_worktree_state,
)


class TestFullCancelCleanupFlow:
    """End-to-end test: create work artifacts, then cancel and verify cleanup."""

    def test_complete_cancel_flow(self, git_repo: Path):
        """Create 3 worktrees/branches, cancel job, verify all cleaned up."""
        # Setup: create worktrees and branches like a real job would
        wt1 = create_worktree(git_repo, "feat/user-auth")
        wt2 = create_worktree(git_repo, "feat/auth-ui")
        wt3 = create_worktree(git_repo, "feat/project-init")

        # Verify they exist
        assert worktree_exists(git_repo, "feat/user-auth")
        assert worktree_exists(git_repo, "feat/auth-ui")
        assert worktree_exists(git_repo, "feat/project-init")
        assert branch_exists(git_repo, "feat/user-auth")
        assert branch_exists(git_repo, "feat/auth-ui")
        assert branch_exists(git_repo, "feat/project-init")

        # Cancel: one PR already merged, two open
        pr_branches = [
            {
                "branch": "feat/project-init",
                "pr_number": 11,
                "worktree_path": wt3.path,
                "merged": True,
            },
            {
                "branch": "feat/user-auth",
                "pr_number": 12,
                "worktree_path": wt1.path,
                "merged": False,
            },
            {
                "branch": "feat/auth-ui",
                "pr_number": 14,
                "worktree_path": wt2.path,
                "merged": False,
            },
        ]

        with patch("devteam.git.cleanup.close_pr") as mock_close:
            with patch("devteam.git.cleanup.delete_remote_branch"):
                result = cleanup_on_cancel(
                    repo_root=git_repo,
                    pr_branches=pr_branches,
                )

        # Verify cleanup
        assert result.success is True

        # Open PRs were closed (merged one is preserved, not closed)
        assert mock_close.call_count == 2

        # Merged PR preserved in the result list
        assert len(result.preserved) == 1
        assert result.preserved[0]["pr_number"] == 11

        # All worktrees removed (including merged -- local artifacts cleaned)
        assert not wt1.path.exists()
        assert not wt2.path.exists()
        assert not wt3.path.exists()

        # Open local branches deleted
        assert not branch_exists(git_repo, "feat/user-auth")
        assert not branch_exists(git_repo, "feat/auth-ui")

        # Merged local branch also deleted (local cleanup for merged)
        assert not branch_exists(git_repo, "feat/project-init")

    def test_cancel_idempotent_double_run(self, git_repo: Path):
        """Running cancel twice produces the same result."""
        wt = create_worktree(git_repo, "feat/double-cancel")

        pr_branches = [
            {
                "branch": "feat/double-cancel",
                "pr_number": 20,
                "worktree_path": wt.path,
                "merged": False,
            },
        ]

        with patch("devteam.git.cleanup.close_pr"):
            with patch("devteam.git.cleanup.delete_remote_branch"):
                result1 = cleanup_on_cancel(repo_root=git_repo, pr_branches=pr_branches)
                result2 = cleanup_on_cancel(repo_root=git_repo, pr_branches=pr_branches)

        assert result1.success is True
        assert result2.success is True

    def test_worktree_with_dirty_files_force_cleaned(self, git_repo: Path):
        """Worktrees with uncommitted changes are force-cleaned on cancel."""
        wt = create_worktree(git_repo, "feat/dirty-cancel")

        # Make the worktree dirty
        (wt.path / "dirty.txt").write_text("uncommitted work")

        state = check_worktree_state(wt.path)
        assert state.clean is False

        pr_branches = [
            {
                "branch": "feat/dirty-cancel",
                "pr_number": 30,
                "worktree_path": wt.path,
                "merged": False,
            },
        ]

        with patch("devteam.git.cleanup.close_pr"):
            with patch("devteam.git.cleanup.delete_remote_branch"):
                result = cleanup_on_cancel(repo_root=git_repo, pr_branches=pr_branches)

        assert result.success is True
        assert not wt.path.exists()
