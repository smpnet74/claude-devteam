"""Branch lifecycle -- create feature branches, delete local+remote.

All operations are idempotent: creating an existing branch or deleting
a non-existent one is a safe no-op.
"""

from __future__ import annotations

from pathlib import Path

from devteam.git.helpers import GitError, git_run

# Branches that must never be deleted
PROTECTED_BRANCHES = frozenset({"main", "master", "develop"})


def create_feature_branch(
    repo_root: Path,
    branch: str,
    base_ref: str = "HEAD",
) -> None:
    """Create a local feature branch.

    Idempotent: if the branch already exists, this is a no-op.

    Args:
        repo_root: Root of the git repo (or worktree).
        branch: Branch name to create.
        base_ref: Git ref to branch from (default HEAD).

    Raises:
        ValueError: If branch name is empty.
        GitError: If the git command fails.
    """
    if not branch:
        raise ValueError("branch must not be empty")

    if branch_exists(repo_root, branch):
        return
    git_run(["branch", branch, base_ref], cwd=repo_root)


def delete_local_branch(
    repo_root: Path,
    branch: str,
    force: bool = False,
) -> None:
    """Delete a local branch.

    Idempotent: if the branch does not exist, this is a no-op.

    Args:
        repo_root: Root of the git repo.
        branch: Branch name to delete.
        force: If True, use -D instead of -d (delete unmerged branch).

    Raises:
        ValueError: If trying to delete a protected branch (main/master/develop).
    """
    if not branch:
        raise ValueError("branch must not be empty")

    if branch in PROTECTED_BRANCHES:
        raise ValueError(f"Cannot delete default branch '{branch}'")

    if not branch_exists(repo_root, branch):
        return

    flag = "-D" if force else "-d"
    git_run(["branch", flag, branch], cwd=repo_root)


def delete_remote_branch(
    repo_root: Path,
    branch: str,
    remote: str = "origin",
) -> None:
    """Delete a remote branch.

    Idempotent: if the remote branch does not exist, this is a no-op.

    Args:
        repo_root: Root of the git repo.
        branch: Branch name to delete on the remote.
        remote: Remote name (default 'origin').

    Raises:
        ValueError: If branch name is empty or is a protected branch.
    """
    if not branch:
        raise ValueError("branch must not be empty")

    if branch in PROTECTED_BRANCHES:
        raise ValueError(f"Cannot delete default branch '{branch}'")

    try:
        git_run(["push", remote, "--delete", branch], cwd=repo_root)
    except GitError as e:
        # Branch already deleted on remote -- idempotent
        if "remote ref does not exist" in e.stderr:
            return
        raise


def branch_exists(repo_root: Path, branch: str) -> bool:
    """Check if a branch exists locally.

    Args:
        repo_root: Root of the git repo.
        branch: Branch name to check.

    Returns:
        True if the branch exists.
    """
    try:
        git_run(["rev-parse", "--verify", f"refs/heads/{branch}"], cwd=repo_root)
        return True
    except GitError:
        return False


def remote_branch_exists(
    repo_root: Path,
    branch: str,
    remote: str = "origin",
) -> bool:
    """Check if a branch exists on the remote.

    Args:
        repo_root: Root of the git repo.
        branch: Branch name to check.
        remote: Remote name (default 'origin').

    Returns:
        True if the branch exists on the remote.
    """
    result = git_run(
        ["ls-remote", "--heads", remote, f"refs/heads/{branch}"],
        cwd=repo_root,
    )
    return bool(result.strip())
