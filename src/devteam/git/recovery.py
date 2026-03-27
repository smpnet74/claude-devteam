"""Idempotent recovery checks for git/GitHub side effects.

Pattern: before any side-effecting step, check if the effect already
happened. Every external action is idempotent on retry.

Used by the DBOS workflow layer to safely resume after crashes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from devteam.git.helpers import GhError, GitError, gh_run, git_run
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
) -> RecoveryCheck:
    """Check if local branch is pushed to remote with matching tip commit.

    Verifies both that the remote branch exists and that local and remote
    tip SHAs match (detects divergence).

    Args:
        repo_root: Root of the git repo.
        branch: Branch name.
        remote: Remote name.

    Returns:
        RecoveryCheck with exists/clean flags and details.
    """
    if not remote_branch_exists(repo_root, branch, remote=remote):
        return RecoveryCheck(exists=False, clean=False, details="Remote branch does not exist")

    # Compare local and remote tip SHAs
    try:
        local_sha = git_run(["rev-parse", branch], cwd=repo_root).strip()
        remote_sha = git_run(["rev-parse", f"{remote}/{branch}"], cwd=repo_root).strip()
        if local_sha == remote_sha:
            return RecoveryCheck(exists=True, clean=True, details="Branch pushed and up to date")
        else:
            return RecoveryCheck(
                exists=True,
                clean=False,
                details=f"Branch diverged: local={local_sha[:8]} remote={remote_sha[:8]}",
            )
    except GitError:
        return RecoveryCheck(exists=True, clean=False, details="Cannot compare branch tips")


def check_pr_exists(
    cwd: Path,
    branch: str,
    upstream_repo: str | None = None,
) -> RecoveryCheck:
    """Check if a PR exists for this branch, including upstream in fork workflows.

    Used before creating a PR to avoid duplicates.

    Args:
        cwd: Working directory.
        branch: Head branch name.
        upstream_repo: If working from a fork, the upstream 'owner/name'.

    Returns:
        RecoveryCheck with exists flag and details.
    """
    pr = find_existing_pr(cwd, branch, repo=upstream_repo)
    if pr is not None:
        return RecoveryCheck(exists=True, clean=True, details=f"PR #{pr.number} found")

    return RecoveryCheck(exists=False, clean=False, details="No PR found")


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
    except GhError:
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
