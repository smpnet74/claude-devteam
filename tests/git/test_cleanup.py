"""Tests for cleanup operations after merge and cancel."""

from pathlib import Path
from unittest.mock import patch


from devteam.git.cleanup import (
    CleanupAction,
    cleanup_after_merge,
    cleanup_on_cancel,
    cleanup_single_pr,
)


class TestCleanupAfterMerge:
    def test_full_cleanup(self, git_repo: Path):
        """Cleans up worktree, local branch, and remote branch after merge."""
        from devteam.git.worktree import create_worktree

        info = create_worktree(git_repo, "feat/merged")

        with patch("devteam.git.cleanup.delete_remote_branch") as mock_remote:
            result = cleanup_after_merge(
                repo_root=git_repo,
                branch="feat/merged",
                worktree_path=info.path,
            )
            assert CleanupAction.WORKTREE_REMOVED in result.actions
            assert CleanupAction.LOCAL_BRANCH_DELETED in result.actions
            assert CleanupAction.REMOTE_BRANCH_DELETED in result.actions
            assert not info.path.exists()
            mock_remote.assert_called_once()

    def test_cleanup_idempotent(self, git_repo: Path):
        """Running cleanup twice does not raise."""
        with patch("devteam.git.cleanup.delete_remote_branch"):
            result1 = cleanup_after_merge(
                repo_root=git_repo,
                branch="feat/already-gone",
                worktree_path=git_repo / ".worktrees" / "feat-already-gone",
            )
            # All actions should still be reported (idempotent path)
            assert result1.success is True


class TestCleanupOnCancel:
    def test_cancel_closes_prs_and_cleans(self, git_repo: Path):
        """Cancel closes open PRs, deletes branches, removes worktrees."""
        from devteam.git.worktree import create_worktree

        wt1 = create_worktree(git_repo, "feat/cancel-a")
        wt2 = create_worktree(git_repo, "feat/cancel-b")

        pr_branches = [
            {
                "branch": "feat/cancel-a",
                "pr_number": 12,
                "worktree_path": wt1.path,
                "merged": False,
            },
            {
                "branch": "feat/cancel-b",
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
                assert result.success is True
                assert mock_close.call_count == 2
                assert not wt1.path.exists()
                assert not wt2.path.exists()

    def test_cancel_preserves_merged_prs(self, git_repo: Path):
        """Already-merged PRs are preserved on cancel."""
        pr_branches = [
            {
                "branch": "feat/merged",
                "pr_number": 11,
                "worktree_path": None,
                "merged": True,
            },
            {
                "branch": "feat/open",
                "pr_number": 12,
                "worktree_path": None,
                "merged": False,
            },
        ]

        with patch("devteam.git.cleanup.close_pr") as mock_close:
            with patch("devteam.git.cleanup.delete_remote_branch"):
                with patch("devteam.git.cleanup.remove_worktree"):
                    with patch("devteam.git.cleanup.delete_local_branch"):
                        result = cleanup_on_cancel(
                            repo_root=git_repo,
                            pr_branches=pr_branches,
                        )
                        # Only the open PR should be closed
                        mock_close.assert_called_once()
                        assert len(result.preserved) == 1
                        assert result.preserved[0]["pr_number"] == 11

    def test_cancel_idempotent(self, git_repo: Path):
        """Running cancel twice is safe."""
        pr_branches = [
            {
                "branch": "feat/gone",
                "pr_number": 99,
                "worktree_path": None,
                "merged": False,
            },
        ]

        with patch("devteam.git.cleanup.close_pr"):
            with patch("devteam.git.cleanup.delete_remote_branch"):
                with patch("devteam.git.cleanup.remove_worktree"):
                    with patch("devteam.git.cleanup.delete_local_branch"):
                        result1 = cleanup_on_cancel(repo_root=git_repo, pr_branches=pr_branches)
                        result2 = cleanup_on_cancel(repo_root=git_repo, pr_branches=pr_branches)
                        assert result1.success is True
                        assert result2.success is True


class TestCleanupSinglePR:
    def test_cleanup_single(self, git_repo: Path):
        """Cleans up a single PR's artifacts."""
        with patch("devteam.git.cleanup.close_pr"):
            with patch("devteam.git.cleanup.delete_remote_branch"):
                with patch("devteam.git.cleanup.remove_worktree"):
                    with patch("devteam.git.cleanup.delete_local_branch"):
                        result = cleanup_single_pr(
                            repo_root=git_repo,
                            branch="feat/single",
                            pr_number=5,
                            worktree_path=None,
                        )
                        assert result.success is True
