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
from urllib.parse import urlparse

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


@dataclass(frozen=True)
class ForkResult:
    """Result of ensure_fork including optional fork NWO."""

    status: ForkStatus
    fork_nwo: str | None = None  # owner/repo of the fork


def _validate_nwo(nwo: str) -> None:
    """Validate owner/repo format."""
    parts = nwo.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Invalid owner/repo format: {nwo!r}")


def check_push_access(repo_nwo: str) -> bool:
    """Check if the authenticated user has push access to a repo.

    Args:
        repo_nwo: Repository in 'owner/name' format (e.g. 'org/repo').

    Returns:
        True if the user can push to the repo.
        False when permissions.push is False or the repo is not found (404).

    Raises:
        ValueError: If repo_nwo is empty or malformed.
        GhError: If the API call fails for reasons other than 404
            (auth, network, etc.).
    """
    _validate_nwo(repo_nwo)

    try:
        result = gh_run(
            ["api", f"repos/{repo_nwo}"],
            parse_json=True,
        )
    except GhError as e:
        # 404 means the repo doesn't exist or the user can't see it -- no push.
        if "HTTP 404" in e.stderr or "Not Found" in e.stderr:
            return False
        raise
    permissions = result.get("permissions", {}) if isinstance(result, dict) else {}
    return permissions.get("push", False)


def find_existing_fork(upstream_nwo: str) -> str | None:
    """Check if the authenticated user already has a fork of the repo.

    Args:
        upstream_nwo: Upstream repo in 'owner/name' format.

    Returns:
        The 'owner/name' of the existing fork, or None.

    Raises:
        ValueError: If upstream_nwo is empty or malformed.
    """
    _validate_nwo(upstream_nwo)

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

    Idempotent: if a fork already exists, returns it without re-forking.

    Args:
        owner: Repository owner.
        repo: Repository name.

    Returns:
        The 'owner/name' of the newly created (or existing) fork.

    Raises:
        ValueError: If owner or repo is empty.
        GhError: If the fork creation fails.
    """
    if not owner:
        raise ValueError("owner must not be empty")
    if not repo:
        raise ValueError("repo must not be empty")

    nwo = f"{owner}/{repo}"
    _validate_nwo(nwo)

    # Idempotent: check if fork already exists
    existing = find_existing_fork(nwo)
    if existing:
        return existing

    gh_run(["repo", "fork", nwo, "--clone=false"])

    # Verify fork was created
    fork_nwo = find_existing_fork(nwo)
    if fork_nwo is None:
        raise GhError(
            ["repo", "fork"],
            1,
            f"Fork of {nwo} was created but could not be found",
        )
    return fork_nwo


def ensure_fork(upstream_nwo: str) -> ForkResult:
    """Ensure the user can push to the repo, forking if necessary.

    Decision tree:
    1. Check push access -> DIRECT
    2. Check for existing fork -> EXISTING_FORK
    3. Create fork via gh -> NEW_FORK

    Args:
        upstream_nwo: Upstream repo in 'owner/name' format.

    Returns:
        ForkResult with status and optional fork NWO.

    Raises:
        ValueError: If upstream_nwo is empty or malformed.
    """
    _validate_nwo(upstream_nwo)

    if check_push_access(upstream_nwo):
        return ForkResult(status=ForkStatus.DIRECT)

    existing = find_existing_fork(upstream_nwo)
    if existing is not None:
        return ForkResult(status=ForkStatus.EXISTING_FORK, fork_nwo=existing)

    owner, repo = upstream_nwo.split("/", 1)
    fork_nwo = create_fork(owner, repo)
    return ForkResult(status=ForkStatus.NEW_FORK, fork_nwo=fork_nwo)


def detect_fork_strategy(repo_root: Path) -> ForkResult:
    """Determine if forking is needed for the current repository.

    Inspects the remote URL to determine the upstream repo, then
    checks push access and fork status.

    Args:
        repo_root: Root of the local git clone.

    Returns:
        ForkResult with status and optional fork NWO.

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

    # Detect existing origin scheme to preserve transport (SSH vs HTTPS)
    try:
        current_url = git_run(["remote", "get-url", "origin"], cwd=repo_root).strip()
        use_ssh = current_url.startswith("git@") or current_url.startswith("ssh://")
    except GitError:
        use_ssh = False

    if use_ssh:
        fork_url = f"git@github.com:{fork_nwo}.git"
        upstream_url = f"git@github.com:{upstream_nwo}.git"
    else:
        fork_url = f"https://github.com/{fork_nwo}.git"
        upstream_url = f"https://github.com/{upstream_nwo}.git"

    # Set origin to the fork
    try:
        git_run(["remote", "set-url", "origin", fork_url], cwd=repo_root)
    except GitError as e:
        if "No such remote" in e.stderr:
            git_run(["remote", "add", "origin", fork_url], cwd=repo_root)
        else:
            raise

    # Set upstream to the original repo
    try:
        git_run(["remote", "set-url", "upstream", upstream_url], cwd=repo_root)
    except GitError as e:
        if "No such remote" in e.stderr:
            git_run(["remote", "add", "upstream", upstream_url], cwd=repo_root)
        else:
            raise


def _parse_nwo_from_url(url: str) -> str:
    """Parse 'owner/repo' from a git remote URL.

    Handles:
        https://github.com/owner/repo.git
        https://github.com/owner/repo
        ssh://git@github.com/owner/repo.git
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

    # SSH: ssh://git@github.com/owner/repo.git
    if url.startswith("ssh://git@github.com/"):
        path = url.split("github.com/", 1)[1].removesuffix(".git")
        if "/" not in path or not path:
            raise ValueError(f"Cannot parse owner/repo from URL: {url}")
        return path

    # SSH format: git@github.com:owner/repo.git
    if url.startswith("git@github.com:"):
        path = url.split(":", 1)[1]
        path = path.removesuffix(".git")
        parts = path.split("/")
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"

    # HTTPS format: https://github.com/owner/repo.git
    parsed = urlparse(url)
    if parsed.hostname == "github.com":
        path = parsed.path.lstrip("/").removesuffix(".git")
        if "/" not in path or not path:
            raise ValueError(f"Cannot parse owner/repo from URL: {url}")
        return path

    raise ValueError(f"Cannot parse owner/repo from URL: {url!r}")
