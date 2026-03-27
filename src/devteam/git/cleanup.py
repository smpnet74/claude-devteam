"""Cleanup operations after merge and on cancel.

Post-merge: remove worktree, delete local branch, delete remote branch.
On cancel: close PRs, then full cleanup for each. Preserves merged PRs.

All operations are idempotent -- safe to run multiple times.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from devteam.git.branch import delete_local_branch, delete_remote_branch
from devteam.git.pr import close_pr
from devteam.git.worktree import remove_worktree


class CleanupAction(Enum):
    """Individual cleanup actions performed."""

    WORKTREE_REMOVED = "worktree_removed"
    LOCAL_BRANCH_DELETED = "local_branch_deleted"
    REMOTE_BRANCH_DELETED = "remote_branch_deleted"
    PR_CLOSED = "pr_closed"


@dataclass
class CleanupResult:
    """Result of a cleanup operation."""

    success: bool = True
    actions: list[CleanupAction] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    preserved: list[dict[str, Any]] = field(default_factory=list)


def _cleanup_local_artifacts(
    repo_root: Path,
    branch: str,
    worktree_path: Path | None,
    result: CleanupResult,
) -> None:
    """Remove local worktree and branch. Shared by merge and cancel paths.

    Args:
        repo_root: Root of the main git repo.
        branch: Branch name.
        worktree_path: Path to the worktree (if it exists).
        result: CleanupResult to accumulate actions/errors into.
    """
    # Remove worktree
    if worktree_path is not None:
        try:
            remove_worktree(repo_root, worktree_path, force=True)
            result.actions.append(CleanupAction.WORKTREE_REMOVED)
        except Exception as e:
            result.errors.append(f"Failed to remove worktree: {e}")

    # Delete local branch
    try:
        delete_local_branch(repo_root, branch, force=True)
        result.actions.append(CleanupAction.LOCAL_BRANCH_DELETED)
    except ValueError:
        # Protected branch -- skip
        pass
    except Exception as e:
        result.errors.append(f"Failed to delete local branch: {e}")


def cleanup_after_merge(
    repo_root: Path,
    branch: str,
    worktree_path: Path | None = None,
    remote: str = "origin",
) -> CleanupResult:
    """Clean up after a PR is merged.

    Removes the worktree, deletes the local branch, and deletes the
    remote branch. All steps are idempotent.

    Args:
        repo_root: Root of the main git repo.
        branch: Branch name that was merged.
        worktree_path: Path to the worktree (if it exists).
        remote: Remote name (default 'origin').

    Returns:
        CleanupResult with list of actions taken.
    """
    result = CleanupResult()

    # 1. Remove worktree and local branch
    _cleanup_local_artifacts(repo_root, branch, worktree_path, result)

    # 2. Delete remote branch
    try:
        delete_remote_branch(repo_root, branch, remote=remote)
        result.actions.append(CleanupAction.REMOTE_BRANCH_DELETED)
    except Exception as e:
        result.errors.append(f"Failed to delete remote branch: {e}")

    result.success = len(result.errors) == 0
    return result


def cleanup_single_pr(
    repo_root: Path,
    branch: str,
    pr_number: int | None = None,
    worktree_path: Path | None = None,
    comment: str = "Cancelled by operator",
) -> CleanupResult:
    """Clean up artifacts for a single PR.

    Closes the PR (if open), removes the worktree, and deletes branches.

    Args:
        repo_root: Root of the main git repo.
        branch: Branch name.
        pr_number: PR number (if a PR was opened).
        worktree_path: Path to the worktree.
        comment: Comment to post on the PR before closing.

    Returns:
        CleanupResult.
    """
    result = CleanupResult()

    # 1. Close PR
    if pr_number is not None:
        try:
            close_pr(repo_root, pr_number, comment=comment)
            result.actions.append(CleanupAction.PR_CLOSED)
        except Exception as e:
            result.errors.append(f"Failed to close PR #{pr_number}: {e}")

    # 2. Remove worktree
    if worktree_path is not None:
        try:
            remove_worktree(repo_root, worktree_path, force=True)
            result.actions.append(CleanupAction.WORKTREE_REMOVED)
        except Exception as e:
            result.errors.append(f"Failed to remove worktree: {e}")

    # 3. Delete local branch
    try:
        delete_local_branch(repo_root, branch, force=True)
        result.actions.append(CleanupAction.LOCAL_BRANCH_DELETED)
    except ValueError:
        pass  # Protected branch
    except Exception as e:
        result.errors.append(f"Failed to delete local branch: {e}")

    # 4. Delete remote branch
    try:
        delete_remote_branch(repo_root, branch)
        result.actions.append(CleanupAction.REMOTE_BRANCH_DELETED)
    except Exception as e:
        result.errors.append(f"Failed to delete remote branch: {e}")

    result.success = len(result.errors) == 0
    return result


def cleanup_on_cancel(
    repo_root: Path,
    pr_branches: list[dict[str, Any]],
    comment: str = "Cancelled by operator",
) -> CleanupResult:
    """Full cleanup when a job is cancelled.

    For each PR/branch:
    - If already merged: preserve (add to preserved list)
    - If open: close PR, delete branches, remove worktree

    Args:
        repo_root: Root of the main git repo.
        pr_branches: List of dicts with keys: branch, pr_number,
                     worktree_path, merged.
        comment: Comment to post on closed PRs.

    Returns:
        CleanupResult with all actions and preserved PRs.
    """
    combined = CleanupResult()

    for entry in pr_branches:
        branch = entry["branch"]
        pr_number = entry.get("pr_number")
        worktree_path = entry.get("worktree_path")
        merged = entry.get("merged", False)

        if isinstance(worktree_path, str):
            worktree_path = Path(worktree_path)

        if merged:
            combined.preserved.append(entry)
            # Still clean local artifacts (worktree, local branch)
            # but preserve the remote branch and PR
            _cleanup_local_artifacts(repo_root, branch, worktree_path, combined)
            continue

        single = cleanup_single_pr(
            repo_root=repo_root,
            branch=branch,
            pr_number=pr_number,
            worktree_path=worktree_path,
            comment=comment,
        )
        combined.actions.extend(single.actions)
        combined.errors.extend(single.errors)

    combined.success = len(combined.errors) == 0
    return combined
