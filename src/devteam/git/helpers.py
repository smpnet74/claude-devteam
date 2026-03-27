"""Thin wrappers around git and gh CLI subprocess calls.

All git/GitHub operations in devteam go through these helpers so that:
1. Error handling is consistent (custom exceptions with stderr context).
2. Tests can mock a single call site.
3. Logging and tracing can be added in one place.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class GitError(Exception):
    """Raised when a git command fails."""

    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"git {' '.join(command)} failed (rc={returncode}): {stderr}")


class GhError(Exception):
    """Raised when a gh CLI command fails."""

    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"gh {' '.join(command)} failed (rc={returncode}): {stderr}")


def git_run(
    args: list[str],
    cwd: Path | str | None = None,
    check: bool = True,
) -> str:
    """Run a git command and return stripped stdout.

    Args:
        args: Arguments after 'git' (e.g. ['status']).
        cwd: Working directory for the command.
        check: If True (default), raise GitError on non-zero exit.

    Returns:
        Stripped stdout string.

    Raises:
        GitError: If the command exits non-zero and check=True.
        ValueError: If args is empty.
    """
    if not args:
        raise ValueError("args must not be empty")

    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise GitError(args, result.returncode, result.stderr.strip())
    return result.stdout.strip()


def gh_run(
    args: list[str],
    cwd: Path | str | None = None,
    check: bool = True,
    parse_json: bool = False,
) -> str | dict[str, Any] | list[Any] | Any:
    """Run a gh CLI command and return stripped stdout.

    Args:
        args: Arguments after 'gh' (e.g. ['pr', 'list']).
        cwd: Working directory for the command.
        check: If True (default), raise GhError on non-zero exit.
        parse_json: If True, parse stdout as JSON and return the result.

    Returns:
        Stripped stdout string, or parsed JSON if parse_json=True.

    Raises:
        GhError: If the command exits non-zero and check=True.
        ValueError: If args is empty.
    """
    if not args:
        raise ValueError("args must not be empty")

    result = subprocess.run(
        ["gh", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise GhError(args, result.returncode, result.stderr.strip())
    stdout = result.stdout.strip()
    if parse_json:
        return json.loads(stdout)
    return stdout


def get_repo_root(cwd: Path | str | None = None) -> Path:
    """Return the root directory of the git repository.

    Args:
        cwd: Working directory to start from.

    Returns:
        Path to the repository root.

    Raises:
        GitError: If cwd is not inside a git repository.
    """
    result = git_run(["rev-parse", "--show-toplevel"], cwd=cwd)
    return Path(result)


def get_current_branch(cwd: Path | str | None = None) -> str:
    """Return the current branch name.

    Args:
        cwd: Working directory inside the repo.

    Returns:
        Current branch name (e.g. 'main', 'feat/login').

    Raises:
        GitError: If not in a git repository or HEAD is detached.
    """
    return git_run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)


def get_default_branch(cwd: Path | str | None = None) -> str:
    """Return the default branch name (main or master).

    Checks local branches. Falls back to 'main' if neither exists.

    Args:
        cwd: Working directory inside the repo.

    Returns:
        'main' or 'master'.
    """
    try:
        git_run(["rev-parse", "--verify", "refs/heads/main"], cwd=cwd)
        return "main"
    except GitError:
        pass
    try:
        git_run(["rev-parse", "--verify", "refs/heads/master"], cwd=cwd)
        return "master"
    except GitError:
        pass
    return "main"
