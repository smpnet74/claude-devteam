"""Tests for worktree management."""

import subprocess
from pathlib import Path

import pytest

from devteam.git.worktree import (
    create_worktree,
    list_worktrees,
    remove_worktree,
    worktree_exists,
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
    # Need at least one commit for worktrees to work
    readme = repo / "README.md"
    readme.write_text("# Test")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return repo


class TestCreateWorktree:
    def test_create_worktree_basic(self, git_repo: Path) -> None:
        """Creates a worktree with a new branch in .worktrees/."""
        info = create_worktree(git_repo, "feat/login")
        assert info.branch == "feat/login"
        assert info.path.exists()
        assert info.path == git_repo / ".worktrees" / "feat-login"

    def test_create_worktree_nested_branch_name(self, git_repo: Path) -> None:
        """Branch names with slashes are converted to dashes in dir name."""
        info = create_worktree(git_repo, "feat/user/auth")
        assert info.path == git_repo / ".worktrees" / "feat-user-auth"
        assert info.path.exists()

    def test_create_worktree_custom_base_dir(self, git_repo: Path) -> None:
        """Supports a custom worktree base directory."""
        info = create_worktree(git_repo, "feat/api", worktree_dir=".wt")
        assert info.path == git_repo / ".wt" / "feat-api"

    def test_create_worktree_idempotent(self, git_repo: Path) -> None:
        """Creating the same worktree twice returns the existing one."""
        info1 = create_worktree(git_repo, "feat/login")
        info2 = create_worktree(git_repo, "feat/login")
        assert info1.path == info2.path
        assert info1.branch == info2.branch

    def test_create_worktree_has_commit(self, git_repo: Path) -> None:
        """Created worktree has a commit hash."""
        info = create_worktree(git_repo, "feat/with-commit")
        assert info.commit is not None
        assert len(info.commit) == 40  # full SHA

    def test_create_worktree_empty_branch_raises(self, git_repo: Path) -> None:
        """Empty branch name raises ValueError."""
        with pytest.raises(ValueError, match="branch must not be empty"):
            create_worktree(git_repo, "")

    def test_create_worktree_has_files(self, git_repo: Path) -> None:
        """Worktree directory contains repo files."""
        info = create_worktree(git_repo, "feat/files")
        assert (info.path / "README.md").exists()


class TestRemoveWorktree:
    def test_remove_worktree(self, git_repo: Path) -> None:
        """Removes a worktree and its directory."""
        info = create_worktree(git_repo, "feat/remove-me")
        assert info.path.exists()
        remove_worktree(git_repo, info.path)
        assert not info.path.exists()

    def test_remove_worktree_idempotent(self, git_repo: Path) -> None:
        """Removing a non-existent worktree does not raise."""
        fake_path = git_repo / ".worktrees" / "nonexistent"
        # Should not raise
        remove_worktree(git_repo, fake_path)

    def test_remove_worktree_force(self, git_repo: Path) -> None:
        """Force removal works even with uncommitted changes."""
        info = create_worktree(git_repo, "feat/dirty")
        dirty_file = info.path / "dirty.txt"
        dirty_file.write_text("uncommitted")
        remove_worktree(git_repo, info.path, force=True)
        assert not info.path.exists()


class TestListWorktrees:
    def test_list_worktrees_empty(self, git_repo: Path) -> None:
        """List returns only the main worktree when no extras exist."""
        trees = list_worktrees(git_repo)
        # The main repo itself is always a worktree
        assert len(trees) >= 1

    def test_list_worktrees_after_create(self, git_repo: Path) -> None:
        """List includes created worktrees."""
        create_worktree(git_repo, "feat/a")
        create_worktree(git_repo, "feat/b")
        trees = list_worktrees(git_repo)
        branches = [t.branch for t in trees]
        assert "feat/a" in branches
        assert "feat/b" in branches

    def test_list_worktrees_main_marked(self, git_repo: Path) -> None:
        """The main worktree is marked with is_main=True."""
        create_worktree(git_repo, "feat/side")
        trees = list_worktrees(git_repo)
        main_trees = [t for t in trees if t.is_main]
        assert len(main_trees) == 1

    def test_worktree_info_frozen(self, git_repo: Path) -> None:
        """WorktreeInfo is frozen (immutable)."""
        info = create_worktree(git_repo, "feat/frozen")
        with pytest.raises(AttributeError):
            info.branch = "other"  # type: ignore[misc]


class TestWorktreeExists:
    def test_exists_true(self, git_repo: Path) -> None:
        create_worktree(git_repo, "feat/check")
        assert worktree_exists(git_repo, "feat/check") is True

    def test_exists_false(self, git_repo: Path) -> None:
        assert worktree_exists(git_repo, "feat/nope") is False
