"""Worktree management -- create, remove, list for a job.

Worktrees provide per-PR-group filesystem isolation so agents
work in separate directories without conflicting with each other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from devteam.git.helpers import git_run


@dataclass(frozen=True)
class WorktreeInfo:
    """Information about a git worktree."""

    path: Path
    branch: str
    is_main: bool = False
    commit: str | None = None


def _branch_to_dirname(branch: str) -> str:
    """Convert a branch name to a safe directory name.

    feat/user/auth -> feat-user-auth

    Raises:
        ValueError: If the resulting name is empty, contains null bytes,
            starts with a dot, or contains spaces.
    """
    name = re.sub(r"[/\\]", "-", branch)
    if not name or "\x00" in name or name.startswith(".") or " " in name:
        raise ValueError(f"Unsafe branch name for directory: {branch!r}")
    return name


def create_worktree(
    repo_root: Path,
    branch: str,
    worktree_dir: str = ".worktrees",
    base_ref: str = "HEAD",
) -> WorktreeInfo:
    """Create a worktree with a new branch.

    Idempotent: if the worktree already exists for this branch, returns
    its info without modification.

    Args:
        repo_root: Root of the main git repo.
        branch: Branch name to create (e.g. 'feat/login').
        worktree_dir: Subdirectory under repo_root for worktrees.
        base_ref: Git ref to branch from (default HEAD).

    Returns:
        WorktreeInfo with the path and branch name.

    Raises:
        ValueError: If branch name is empty.
        GitError: If the git worktree command fails.
    """
    if not branch:
        raise ValueError("branch must not be empty")

    dirname = _branch_to_dirname(branch)
    wt_path = repo_root / worktree_dir / dirname

    # Idempotency: check if this worktree already exists
    if worktree_exists(repo_root, branch):
        commit = git_run(["rev-parse", "HEAD"], cwd=wt_path)
        return WorktreeInfo(path=wt_path, branch=branch, commit=commit)

    # Ensure the worktree base directory exists
    (repo_root / worktree_dir).mkdir(parents=True, exist_ok=True)

    git_run(
        ["worktree", "add", str(wt_path), "-b", branch, base_ref],
        cwd=repo_root,
    )
    commit = git_run(["rev-parse", "HEAD"], cwd=wt_path)
    return WorktreeInfo(path=wt_path, branch=branch, commit=commit)


def remove_worktree(
    repo_root: Path,
    worktree_path: Path,
    force: bool = False,
) -> None:
    """Remove a worktree.

    Idempotent: if the worktree does not exist, this is a no-op.

    Args:
        repo_root: Root of the main git repo.
        worktree_path: Path to the worktree directory.
        force: If True, force removal even with uncommitted changes.
    """
    if not worktree_path.exists():
        # Already removed -- idempotent
        return

    args = ["worktree", "remove", str(worktree_path)]
    if force:
        args.insert(2, "--force")

    git_run(args, cwd=repo_root)


def list_worktrees(repo_root: Path) -> list[WorktreeInfo]:
    """List all worktrees for a repo.

    Args:
        repo_root: Root of the main git repo.

    Returns:
        List of WorktreeInfo for all worktrees (including the main one).
    """
    output = git_run(["worktree", "list", "--porcelain"], cwd=repo_root)
    worktrees: list[WorktreeInfo] = []
    current_path: Path | None = None
    current_branch: str | None = None
    current_commit: str | None = None
    is_bare = False

    for line in output.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line.split(" ", 1)[1])
        elif line.startswith("HEAD "):
            current_commit = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            # branch refs/heads/feat/login -> feat/login
            ref = line.split(" ", 1)[1]
            current_branch = ref.removeprefix("refs/heads/")
        elif line == "bare":
            is_bare = True
        elif line == "" and current_path is not None:
            if not is_bare:
                # Determine if this is the main worktree (first entry is always main)
                is_main = len(worktrees) == 0
                worktrees.append(
                    WorktreeInfo(
                        path=current_path,
                        branch=current_branch or "",
                        is_main=is_main,
                        commit=current_commit,
                    )
                )
            current_path = None
            current_branch = None
            current_commit = None
            is_bare = False

    # Handle last entry (no trailing blank line)
    if current_path is not None and not is_bare:
        is_main = len(worktrees) == 0
        worktrees.append(
            WorktreeInfo(
                path=current_path,
                branch=current_branch or "",
                is_main=is_main,
                commit=current_commit,
            )
        )

    return worktrees


def worktree_exists(repo_root: Path, branch: str) -> bool:
    """Check if a worktree exists for the given branch.

    Args:
        repo_root: Root of the main git repo.
        branch: Branch name to check.

    Returns:
        True if a worktree with that branch exists.
    """
    trees = list_worktrees(repo_root)
    return any(t.branch == branch for t in trees)
