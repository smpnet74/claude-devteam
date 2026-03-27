"""Tests for branch lifecycle management."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from devteam.git.branch import (
    branch_exists,
    create_feature_branch,
    delete_local_branch,
    delete_remote_branch,
    remote_branch_exists,
)
from devteam.git.helpers import GitError, get_current_branch, get_default_branch


class TestCreateFeatureBranch:
    def test_create_branch(self, git_repo: Path) -> None:
        """Creates a local branch from HEAD."""
        create_feature_branch(git_repo, "feat/new-thing")
        assert branch_exists(git_repo, "feat/new-thing")

    def test_create_branch_from_ref(self, git_repo: Path) -> None:
        """Creates a branch from a specific ref."""
        # Make a second commit
        f = git_repo / "second.txt"
        f.write_text("second")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "second"],
            check=True,
            capture_output=True,
        )
        first_commit = subprocess.run(
            ["git", "-C", str(git_repo), "rev-parse", "HEAD~1"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        create_feature_branch(git_repo, "feat/from-first", base_ref=first_commit)
        assert branch_exists(git_repo, "feat/from-first")

    def test_create_branch_idempotent(self, git_repo: Path) -> None:
        """Creating an existing branch is a no-op."""
        create_feature_branch(git_repo, "feat/idempotent")
        # Should not raise
        create_feature_branch(git_repo, "feat/idempotent")
        assert branch_exists(git_repo, "feat/idempotent")

    def test_create_branch_empty_name_raises(self, git_repo: Path) -> None:
        """Empty branch name raises ValueError."""
        with pytest.raises(ValueError, match="branch must not be empty"):
            create_feature_branch(git_repo, "")


class TestDeleteLocalBranch:
    def test_delete_local_branch(self, git_repo: Path) -> None:
        """Deletes a local branch."""
        create_feature_branch(git_repo, "feat/delete-me")
        delete_local_branch(git_repo, "feat/delete-me", force=True)
        assert not branch_exists(git_repo, "feat/delete-me")

    def test_delete_local_branch_idempotent(self, git_repo: Path) -> None:
        """Deleting a non-existent branch is a no-op."""
        # Should not raise
        delete_local_branch(git_repo, "feat/never-existed")

    def test_refuses_to_delete_default_branch(self, git_repo: Path) -> None:
        """Cannot delete main/master."""
        with pytest.raises(ValueError, match="default branch"):
            delete_local_branch(git_repo, "main")

    def test_refuses_to_delete_develop(self, git_repo: Path) -> None:
        """Cannot delete develop."""
        with pytest.raises(ValueError, match="default branch"):
            delete_local_branch(git_repo, "develop")

    def test_empty_branch_raises(self, git_repo: Path) -> None:
        """Empty branch name raises ValueError."""
        with pytest.raises(ValueError, match="branch must not be empty"):
            delete_local_branch(git_repo, "")


class TestDeleteRemoteBranch:
    def test_delete_remote_branch_mocked(self, git_repo: Path) -> None:
        """Deletes remote branch via git push --delete (mocked)."""
        with patch("devteam.git.branch.git_run") as mock_git:
            delete_remote_branch(git_repo, "feat/remote-branch")
            mock_git.assert_called_once_with(
                ["push", "origin", "--delete", "feat/remote-branch"],
                cwd=git_repo,
            )

    def test_delete_remote_branch_idempotent(self, git_repo: Path) -> None:
        """Deleting an already-deleted remote branch is a no-op."""
        with patch("devteam.git.branch.git_run") as mock_git:
            mock_git.side_effect = GitError(
                ["push", "origin", "--delete", "feat/gone"],
                1,
                "error: unable to delete 'feat/gone': remote ref does not exist",
            )
            # Should not raise
            delete_remote_branch(git_repo, "feat/gone")

    def test_delete_remote_branch_custom_remote(self, git_repo: Path) -> None:
        """Supports custom remote name."""
        with patch("devteam.git.branch.git_run") as mock_git:
            delete_remote_branch(git_repo, "feat/x", remote="upstream")
            mock_git.assert_called_once_with(
                ["push", "upstream", "--delete", "feat/x"],
                cwd=git_repo,
            )

    def test_delete_remote_branch_empty_raises(self, git_repo: Path) -> None:
        """Empty branch name raises ValueError."""
        with pytest.raises(ValueError, match="branch must not be empty"):
            delete_remote_branch(git_repo, "")

    def test_refuses_to_delete_remote_main(self, git_repo: Path) -> None:
        """Cannot delete main on remote."""
        with pytest.raises(ValueError, match="default branch"):
            delete_remote_branch(git_repo, "main")

    def test_refuses_to_delete_remote_master(self, git_repo: Path) -> None:
        """Cannot delete master on remote."""
        with pytest.raises(ValueError, match="default branch"):
            delete_remote_branch(git_repo, "master")

    def test_refuses_to_delete_remote_develop(self, git_repo: Path) -> None:
        """Cannot delete develop on remote."""
        with pytest.raises(ValueError, match="default branch"):
            delete_remote_branch(git_repo, "develop")


class TestBranchQueries:
    def test_branch_exists_true(self, git_repo: Path) -> None:
        create_feature_branch(git_repo, "feat/exists")
        assert branch_exists(git_repo, "feat/exists") is True

    def test_branch_exists_false(self, git_repo: Path) -> None:
        assert branch_exists(git_repo, "feat/nope") is False

    def test_get_current_branch(self, git_repo: Path) -> None:
        branch = get_current_branch(cwd=git_repo)
        # Initial branch is main or master depending on git config
        assert branch in ("main", "master")

    def test_get_default_branch(self, git_repo: Path) -> None:
        branch = get_default_branch(cwd=git_repo)
        assert branch in ("main", "master")

    def test_remote_branch_exists_mocked_true(self, git_repo: Path) -> None:
        """remote_branch_exists calls ls-remote (mocked) -- found."""
        with patch("devteam.git.branch.git_run") as mock_git:
            mock_git.return_value = "abc123\trefs/heads/feat/x"
            assert remote_branch_exists(git_repo, "feat/x") is True

    def test_remote_branch_exists_mocked_false(self, git_repo: Path) -> None:
        """remote_branch_exists calls ls-remote (mocked) -- not found."""
        with patch("devteam.git.branch.git_run") as mock_git:
            mock_git.return_value = ""
            assert remote_branch_exists(git_repo, "feat/y") is False
