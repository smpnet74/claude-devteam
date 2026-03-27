"""Fork detection and management for open-source contributions.

Handles the three access scenarios:
1. DIRECT -- user has push access (collaborator or own repo).
2. EXISTING_FORK -- user already forked the repo.
3. NEW_FORK -- auto-fork via `gh repo fork`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

from devteam.git.helpers import GhError, GitError, gh_run, git_run


@dataclass(frozen=True)
class ForkInfo:
    """Information about a repository fork."""

    owner: str
    repo: str
    clone_url: str
    is_fork: bool
    parent_owner: str | None = None
    parent_repo: str | None = None


class ForkStatus(Enum):
    """Result of fork detection."""

    DIRECT = "direct"  # Push access to the original repo
    EXISTING_FORK = "existing_fork"  # User already has a fork
    NEW_FORK = "new_fork"  # Fork was just created


def check_push_access(repo_nwo: str) -> bool:
    """Check if the authenticated user has push access to a repo.

    Args:
        repo_nwo: Repository in 'owner/name' format (e.g. 'org/repo').

    Returns:
        True if the user can push to the repo.

    Raises:
        ValueError: If repo_nwo is empty or malformed.
    """
    if not repo_nwo or "/" not in repo_nwo:
        raise ValueError(f"repo_nwo must be in 'owner/name' format, got: {repo_nwo!r}")

    try:
        result = gh_run(
            ["api", f"repos/{repo_nwo}"],
            parse_json=True,
        )
        permissions = result.get("permissions", {}) if isinstance(result, dict) else {}
        return permissions.get("push", False)
    except GhError:
        return False


def find_existing_fork(upstream_nwo: str) -> str | None:
    """Check if the authenticated user already has a fork of the repo.

    Args:
        upstream_nwo: Upstream repo in 'owner/name' format.

    Returns:
        The 'owner/name' of the existing fork, or None.

    Raises:
        ValueError: If upstream_nwo is empty or malformed.
    """
    if not upstream_nwo or "/" not in upstream_nwo:
        raise ValueError(f"upstream_nwo must be in 'owner/name' format, got: {upstream_nwo!r}")

    try:
        result = gh_run(
            [
                "repo",
                "list",
                "--fork",
                "--json",
                "nameWithOwner,parent",
                "--limit",
                "200",
            ],
            parse_json=True,
        )
        repos = cast(list[dict[str, Any]], result)
    except GhError:
        return None

    for repo in repos:
        parent: dict[str, Any] = repo.get("parent", {})
        if parent.get("nameWithOwner") == upstream_nwo:
            return str(repo["nameWithOwner"])
    return None


def create_fork(owner: str, repo: str) -> str:
    """Create a fork of the given repository.

    Args:
        owner: Repository owner.
        repo: Repository name.

    Returns:
        The 'owner/name' of the newly created fork.

    Raises:
        ValueError: If owner or repo is empty.
        GhError: If the fork creation fails.
    """
    if not owner:
        raise ValueError("owner must not be empty")
    if not repo:
        raise ValueError("repo must not be empty")

    gh_run(["repo", "fork", f"{owner}/{repo}", "--clone=false"])
    # After forking, find the newly created fork
    fork_nwo = find_existing_fork(f"{owner}/{repo}")
    if fork_nwo is None:
        # Fallback: gh creates forks under the current user
        # The fork should exist after creation
        raise GhError(
            ["repo", "fork"],
            1,
            f"Fork of {owner}/{repo} was created but could not be found",
        )
    return fork_nwo


def ensure_fork(upstream_nwo: str) -> ForkStatus:
    """Ensure the user can push to the repo, forking if necessary.

    Decision tree:
    1. Check push access -> DIRECT
    2. Check for existing fork -> EXISTING_FORK
    3. Create fork via gh -> NEW_FORK

    Args:
        upstream_nwo: Upstream repo in 'owner/name' format.

    Returns:
        ForkStatus indicating the access method.

    Raises:
        ValueError: If upstream_nwo is empty or malformed.
    """
    if not upstream_nwo or "/" not in upstream_nwo:
        raise ValueError(f"upstream_nwo must be in 'owner/name' format, got: {upstream_nwo!r}")

    if check_push_access(upstream_nwo):
        return ForkStatus.DIRECT

    existing = find_existing_fork(upstream_nwo)
    if existing is not None:
        return ForkStatus.EXISTING_FORK

    gh_run(["repo", "fork", upstream_nwo, "--clone=false"])
    return ForkStatus.NEW_FORK


def detect_fork_strategy(repo_root: Path) -> ForkStatus:
    """Determine if forking is needed for the current repository.

    Inspects the remote URL to determine the upstream repo, then
    checks push access and fork status.

    Args:
        repo_root: Root of the local git clone.

    Returns:
        ForkStatus indicating the recommended strategy.

    Raises:
        ValueError: If the remote URL cannot be parsed.
    """
    remote_url = git_run(["remote", "get-url", "origin"], cwd=repo_root)

    # Parse owner/repo from remote URL
    # Handles: https://github.com/owner/repo.git, git@github.com:owner/repo.git
    nwo = _parse_nwo_from_url(remote_url)
    return ensure_fork(nwo)


def setup_fork_remotes(
    repo_root: Path,
    upstream_nwo: str,
    fork_nwo: str,
) -> None:
    """Configure remotes for a forked repo.

    Sets 'origin' to the fork and 'upstream' to the original repo.
    Idempotent: safe to call multiple times.

    Args:
        repo_root: Root of the local git clone.
        upstream_nwo: Upstream repo in 'owner/name' format.
        fork_nwo: Fork repo in 'owner/name' format.

    Raises:
        ValueError: If either NWO is empty.
    """
    if not upstream_nwo:
        raise ValueError("upstream_nwo must not be empty")
    if not fork_nwo:
        raise ValueError("fork_nwo must not be empty")

    fork_url = f"https://github.com/{fork_nwo}.git"
    upstream_url = f"https://github.com/{upstream_nwo}.git"

    # Set origin to the fork
    try:
        git_run(["remote", "set-url", "origin", fork_url], cwd=repo_root)
    except GitError:
        git_run(["remote", "add", "origin", fork_url], cwd=repo_root)

    # Set upstream to the original repo
    try:
        git_run(["remote", "set-url", "upstream", upstream_url], cwd=repo_root)
    except GitError:
        git_run(["remote", "add", "upstream", upstream_url], cwd=repo_root)


def _parse_nwo_from_url(url: str) -> str:
    """Parse 'owner/repo' from a git remote URL.

    Handles:
        https://github.com/owner/repo.git
        https://github.com/owner/repo
        git@github.com:owner/repo.git
        git@github.com:owner/repo

    Args:
        url: Git remote URL.

    Returns:
        'owner/repo' string.

    Raises:
        ValueError: If the URL cannot be parsed.
    """
    url = url.strip()

    # SSH format: git@github.com:owner/repo.git
    if url.startswith("git@github.com:"):
        path = url.split(":", 1)[1]
        path = path.removesuffix(".git")
        parts = path.split("/")
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"

    # HTTPS format: https://github.com/owner/repo.git
    if "://github.com/" in url:
        # Remove protocol and host
        path = url.split("github.com/", 1)[-1]
        path = path.removesuffix(".git")
        parts = path.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"

    raise ValueError(f"Cannot parse owner/repo from URL: {url!r}")
