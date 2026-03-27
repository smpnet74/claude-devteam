"""Idempotent recovery checks for git/GitHub side effects.

Pattern: before any side-effecting step, check if the effect already
happened. Every external action is idempotent on retry.

Used by the DBOS workflow layer to safely resume after crashes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from devteam.git.helpers import GitError, gh_run, git_run
from devteam.git.branch import remote_branch_exists
from devteam.git.pr import find_existing_pr


@dataclass
class RecoveryCheck:
    """Result of checking a worktree's state for recovery."""

    exists: bool = True
    clean: bool = True
    details: str = ""


def check_worktree_state(worktree_path: Path) -> RecoveryCheck:
    """Check whether a worktree exists and is clean.

    Used before retrying a failed agent step -- if the worktree is dirty,
    it should be reset before re-running.

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        RecoveryCheck with exists and clean flags.
    """
    if not worktree_path.exists():
        return RecoveryCheck(exists=False, clean=False, details="path does not exist")

    try:
        status = git_run(["status", "--porcelain"], cwd=worktree_path)
    except GitError as e:
        return RecoveryCheck(exists=True, clean=False, details=str(e))

    if status:
        return RecoveryCheck(exists=True, clean=False, details=status)
    return RecoveryCheck(exists=True, clean=True)


def check_branch_pushed(
    repo_root: Path,
    branch: str,
    remote: str = "origin",
) -> bool:
    """Check if a branch has been pushed to the remote.

    Used before pushing to avoid redundant pushes.

    Args:
        repo_root: Root of the git repo.
        branch: Branch name.
        remote: Remote name.

    Returns:
        True if the branch exists on the remote.
    """
    return remote_branch_exists(repo_root, branch, remote=remote)


def check_pr_exists(cwd: Path, branch: str) -> int | None:
    """Check if a PR already exists for a branch.

    Used before creating a PR to avoid duplicates.

    Args:
        cwd: Working directory.
        branch: Head branch name.

    Returns:
        PR number if one exists, None otherwise.
    """
    pr = find_existing_pr(cwd, branch)
    if pr is not None:
        return pr.number
    return None


def check_pr_merged(cwd: Path, pr_number: int) -> bool:
    """Check if a PR has already been merged.

    Used before attempting a merge to skip already-merged PRs.

    Args:
        cwd: Working directory.
        pr_number: PR number.

    Returns:
        True if the PR is merged.
    """
    try:
        data = cast(
            dict[str, Any],
            gh_run(
                ["pr", "view", str(pr_number), "--json", "state"],
                cwd=cwd,
                parse_json=True,
            ),
        )
        return data.get("state") == "MERGED"
    except Exception:
        return False


def reset_worktree_to_clean(worktree_path: Path) -> None:
    """Reset a worktree to the last known clean commit.

    Discards all staged and unstaged changes. Used before retrying
    a failed agent step so the agent starts from a clean state.

    Idempotent: resetting an already-clean worktree is a no-op.

    Args:
        worktree_path: Path to the worktree.
    """
    git_run(["reset", "--hard", "HEAD"], cwd=worktree_path)
    git_run(["clean", "-fd"], cwd=worktree_path)
