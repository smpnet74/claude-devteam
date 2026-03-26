# Plan 4: Git Lifecycle Management Implementation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build the complete git lifecycle — worktrees, branches, PRs, forks, cleanup — with idempotent recovery.

**Architecture:** Six modules under `src/devteam/git/` handle discrete lifecycle concerns: worktree management (create/remove/list in `.worktrees/`), branch lifecycle (create feature branches, delete local+remote after merge), PR operations via `gh` CLI (create, check status, merge, feedback loop with session resumption), fork detection (push access check, existing fork lookup, auto-fork), cleanup (post-merge and post-cancel full teardown), and recovery (check-before-act idempotency for all git/GitHub side effects). All subprocess calls go through a shared `_run_git()` / `_run_gh()` helper that captures output, raises on failure, and is easily mockable in tests. The PR feedback loop implements diff-only feedback, CodeRabbit comment categorization, and a configurable circuit breaker.

**Tech Stack:** Python 3.12+, subprocess (git CLI, gh CLI), pytest, pytest tmp_path (temporary git repos), unittest.mock (subprocess mocking for gh calls)

---

## Task 1: Package scaffolding and git/gh subprocess helpers

**Files:**
- Create: `src/devteam/git/__init__.py`
- Create: `src/devteam/git/_subprocess.py`
- Create: `tests/git/__init__.py`
- Create: `tests/git/test_subprocess.py`

- [ ] **Step 1 (2 min):** Create the `src/devteam/git/` package and test directory.

```bash
mkdir -p src/devteam/git
touch src/devteam/git/__init__.py
mkdir -p tests/git
touch tests/git/__init__.py
```

- [ ] **Step 2 (3 min):** Write the failing test for subprocess helpers.

`tests/git/test_subprocess.py`:
```python
"""Tests for git/gh subprocess helpers."""

import subprocess
from unittest.mock import patch

import pytest

from devteam.git._subprocess import run_git, run_gh, GitError, GhError


class TestRunGit:
    def test_run_git_success(self, tmp_path):
        """run_git returns stdout on success."""
        # Create a real git repo to test against
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        result = run_git(["status"], cwd=tmp_path)
        assert "On branch" in result

    def test_run_git_failure_raises(self, tmp_path):
        """run_git raises GitError on non-zero exit."""
        with pytest.raises(GitError, match="not a git repository|fatal"):
            # tmp_path is not a git repo unless we init it
            empty = tmp_path / "empty"
            empty.mkdir()
            run_git(["log"], cwd=empty)

    def test_run_git_strips_output(self, tmp_path):
        """run_git strips trailing whitespace/newlines."""
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        result = run_git(["rev-parse", "--git-dir"], cwd=tmp_path)
        assert result == ".git"


class TestRunGh:
    def test_run_gh_success(self):
        """run_gh returns stdout on success (mocked)."""
        with patch("devteam.git._subprocess.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh", "pr", "list"],
                returncode=0,
                stdout='[{"number": 1}]',
                stderr="",
            )
            result = run_gh(["pr", "list", "--json", "number"])
            assert '"number": 1' in result

    def test_run_gh_failure_raises(self):
        """run_gh raises GhError on non-zero exit."""
        with patch("devteam.git._subprocess.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh", "pr", "view"],
                returncode=1,
                stdout="",
                stderr="no pull requests found",
            )
            with pytest.raises(GhError, match="no pull requests found"):
                run_gh(["pr", "view", "999"])

    def test_run_gh_json_parsing(self):
        """run_gh with parse_json=True returns parsed dict."""
        with patch("devteam.git._subprocess.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh"],
                returncode=0,
                stdout='{"merged": true}',
                stderr="",
            )
            result = run_gh(["pr", "view", "1", "--json", "merged"], parse_json=True)
            assert result == {"merged": True}
```

Run:
```bash
pixi run pytest tests/git/test_subprocess.py -x
```
Expected: All tests fail (ImportError).

- [ ] **Step 3 (4 min):** Implement the subprocess helpers.

`src/devteam/git/_subprocess.py`:
```python
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

    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        self.git_args = args
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"git {' '.join(args)} failed (rc={returncode}): {stderr}")


class GhError(Exception):
    """Raised when a gh CLI command fails."""

    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        self.gh_args = args
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"gh {' '.join(args)} failed (rc={returncode}): {stderr}")


def run_git(
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
    """
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise GitError(args, result.returncode, result.stderr.strip())
    return result.stdout.strip()


def run_gh(
    args: list[str],
    cwd: Path | str | None = None,
    check: bool = True,
    parse_json: bool = False,
) -> str | dict | list | Any:
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
    """
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
```

Run:
```bash
pixi run pytest tests/git/test_subprocess.py -x -v
```
Expected: All 6 tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/devteam/git/ tests/git/
git commit -m "feat: add git/gh subprocess helpers with typed exceptions"
```

---

## Task 2: Worktree management — create, remove, list

**Files:**
- Create: `src/devteam/git/worktree.py`
- Create: `tests/git/test_worktree.py`

- [ ] **Step 1 (3 min):** Write the failing tests.

`tests/git/test_worktree.py`:
```python
"""Tests for worktree management."""

import subprocess
from pathlib import Path

import pytest

from devteam.git.worktree import (
    WorktreeInfo,
    create_worktree,
    remove_worktree,
    list_worktrees,
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
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    # Need at least one commit for worktrees to work
    readme = repo / "README.md"
    readme.write_text("# Test")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


class TestCreateWorktree:
    def test_create_worktree_basic(self, git_repo: Path):
        """Creates a worktree with a new branch in .worktrees/."""
        info = create_worktree(git_repo, "feat/login")
        assert info.branch == "feat/login"
        assert info.path.exists()
        assert info.path == git_repo / ".worktrees" / "feat-login"

    def test_create_worktree_nested_branch_name(self, git_repo: Path):
        """Branch names with slashes are converted to dashes in dir name."""
        info = create_worktree(git_repo, "feat/user/auth")
        assert info.path == git_repo / ".worktrees" / "feat-user-auth"
        assert info.path.exists()

    def test_create_worktree_custom_base_dir(self, git_repo: Path):
        """Supports a custom worktree base directory."""
        info = create_worktree(git_repo, "feat/api", worktree_dir=".wt")
        assert info.path == git_repo / ".wt" / "feat-api"

    def test_create_worktree_idempotent(self, git_repo: Path):
        """Creating the same worktree twice returns the existing one."""
        info1 = create_worktree(git_repo, "feat/login")
        info2 = create_worktree(git_repo, "feat/login")
        assert info1.path == info2.path
        assert info1.branch == info2.branch


class TestRemoveWorktree:
    def test_remove_worktree(self, git_repo: Path):
        """Removes a worktree and its directory."""
        info = create_worktree(git_repo, "feat/remove-me")
        assert info.path.exists()
        remove_worktree(git_repo, info.path)
        assert not info.path.exists()

    def test_remove_worktree_idempotent(self, git_repo: Path):
        """Removing a non-existent worktree does not raise."""
        fake_path = git_repo / ".worktrees" / "nonexistent"
        # Should not raise
        remove_worktree(git_repo, fake_path)

    def test_remove_worktree_force(self, git_repo: Path):
        """Force removal works even with uncommitted changes."""
        info = create_worktree(git_repo, "feat/dirty")
        dirty_file = info.path / "dirty.txt"
        dirty_file.write_text("uncommitted")
        remove_worktree(git_repo, info.path, force=True)
        assert not info.path.exists()


class TestListWorktrees:
    def test_list_worktrees_empty(self, git_repo: Path):
        """List returns only the main worktree when no extras exist."""
        trees = list_worktrees(git_repo)
        # The main repo itself is always a worktree
        assert len(trees) >= 1

    def test_list_worktrees_after_create(self, git_repo: Path):
        """List includes created worktrees."""
        create_worktree(git_repo, "feat/a")
        create_worktree(git_repo, "feat/b")
        trees = list_worktrees(git_repo)
        branches = [t.branch for t in trees]
        assert "feat/a" in branches
        assert "feat/b" in branches


class TestWorktreeExists:
    def test_exists_true(self, git_repo: Path):
        create_worktree(git_repo, "feat/check")
        assert worktree_exists(git_repo, "feat/check") is True

    def test_exists_false(self, git_repo: Path):
        assert worktree_exists(git_repo, "feat/nope") is False
```

Run:
```bash
pixi run pytest tests/git/test_worktree.py -x
```
Expected: All tests fail (ImportError).

- [ ] **Step 2 (5 min):** Implement worktree management.

`src/devteam/git/worktree.py`:
```python
"""Worktree management — create, remove, list for a job.

Worktrees provide per-PR-group filesystem isolation so agents
work in separate directories without conflicting with each other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from devteam.git._subprocess import GitError, run_git


@dataclass(frozen=True)
class WorktreeInfo:
    """Information about a git worktree."""

    path: Path
    branch: str
    commit: str | None = None


def _branch_to_dirname(branch: str) -> str:
    """Convert a branch name to a safe directory name.

    feat/user/auth -> feat-user-auth
    """
    return re.sub(r"[/\\]", "-", branch)


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
    """
    dirname = _branch_to_dirname(branch)
    wt_path = repo_root / worktree_dir / dirname

    # Idempotency: check if this worktree already exists
    if worktree_exists(repo_root, branch):
        commit = run_git(["rev-parse", "HEAD"], cwd=wt_path)
        return WorktreeInfo(path=wt_path, branch=branch, commit=commit)

    # Ensure the worktree base directory exists
    (repo_root / worktree_dir).mkdir(parents=True, exist_ok=True)

    run_git(
        ["worktree", "add", str(wt_path), "-b", branch, base_ref],
        cwd=repo_root,
    )
    commit = run_git(["rev-parse", "HEAD"], cwd=wt_path)
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
        # Already removed — idempotent
        return

    args = ["worktree", "remove", str(worktree_path)]
    if force:
        args.insert(2, "--force")

    try:
        run_git(args, cwd=repo_root)
    except GitError:
        if force:
            raise
        # If non-force removal fails (dirty worktree), caller can retry with force
        raise


def list_worktrees(repo_root: Path) -> list[WorktreeInfo]:
    """List all worktrees for a repo.

    Args:
        repo_root: Root of the main git repo.

    Returns:
        List of WorktreeInfo for all worktrees (including the main one).
    """
    output = run_git(["worktree", "list", "--porcelain"], cwd=repo_root)
    worktrees: list[WorktreeInfo] = []
    current_path: Path | None = None
    current_branch: str | None = None
    current_commit: str | None = None

    for line in output.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line.split(" ", 1)[1])
        elif line.startswith("HEAD "):
            current_commit = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            # branch refs/heads/feat/login -> feat/login
            ref = line.split(" ", 1)[1]
            current_branch = ref.removeprefix("refs/heads/")
        elif line == "" and current_path is not None:
            worktrees.append(
                WorktreeInfo(
                    path=current_path,
                    branch=current_branch or "",
                    commit=current_commit,
                )
            )
            current_path = None
            current_branch = None
            current_commit = None

    # Handle last entry (no trailing blank line)
    if current_path is not None:
        worktrees.append(
            WorktreeInfo(
                path=current_path,
                branch=current_branch or "",
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
```

Run:
```bash
pixi run pytest tests/git/test_worktree.py -x -v
```
Expected: All 10 tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/devteam/git/worktree.py tests/git/test_worktree.py
git commit -m "feat: worktree create/remove/list with idempotent create"
```

---

## Task 3: Branch lifecycle — create, delete local+remote

**Files:**
- Create: `src/devteam/git/branch.py`
- Create: `tests/git/test_branch.py`

- [ ] **Step 1 (3 min):** Write the failing tests.

`tests/git/test_branch.py`:
```python
"""Tests for branch lifecycle management."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from devteam.git.branch import (
    create_feature_branch,
    delete_local_branch,
    delete_remote_branch,
    branch_exists_local,
    branch_exists_remote,
    get_current_branch,
    get_default_branch,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    readme = repo / "README.md"
    readme.write_text("# Test")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


class TestCreateFeatureBranch:
    def test_create_branch(self, git_repo: Path):
        """Creates a local branch from HEAD."""
        create_feature_branch(git_repo, "feat/new-thing")
        assert branch_exists_local(git_repo, "feat/new-thing")

    def test_create_branch_from_ref(self, git_repo: Path):
        """Creates a branch from a specific ref."""
        # Make a second commit
        f = git_repo / "second.txt"
        f.write_text("second")
        subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "second"],
            check=True, capture_output=True,
        )
        first_commit = subprocess.run(
            ["git", "-C", str(git_repo), "rev-parse", "HEAD~1"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        create_feature_branch(git_repo, "feat/from-first", base_ref=first_commit)
        assert branch_exists_local(git_repo, "feat/from-first")

    def test_create_branch_idempotent(self, git_repo: Path):
        """Creating an existing branch is a no-op."""
        create_feature_branch(git_repo, "feat/idempotent")
        # Should not raise
        create_feature_branch(git_repo, "feat/idempotent")
        assert branch_exists_local(git_repo, "feat/idempotent")


class TestDeleteLocalBranch:
    def test_delete_local_branch(self, git_repo: Path):
        """Deletes a local branch."""
        create_feature_branch(git_repo, "feat/delete-me")
        delete_local_branch(git_repo, "feat/delete-me")
        assert not branch_exists_local(git_repo, "feat/delete-me")

    def test_delete_local_branch_idempotent(self, git_repo: Path):
        """Deleting a non-existent branch is a no-op."""
        # Should not raise
        delete_local_branch(git_repo, "feat/never-existed")

    def test_refuses_to_delete_default_branch(self, git_repo: Path):
        """Cannot delete main/master."""
        with pytest.raises(ValueError, match="default branch"):
            delete_local_branch(git_repo, "main")


class TestDeleteRemoteBranch:
    def test_delete_remote_branch_mocked(self, git_repo: Path):
        """Deletes remote branch via git push --delete (mocked)."""
        with patch("devteam.git.branch.run_git") as mock_git:
            delete_remote_branch(git_repo, "feat/remote-branch")
            mock_git.assert_called_once_with(
                ["push", "origin", "--delete", "feat/remote-branch"],
                cwd=git_repo,
            )

    def test_delete_remote_branch_idempotent(self, git_repo: Path):
        """Deleting an already-deleted remote branch is a no-op."""
        with patch("devteam.git.branch.run_git") as mock_git:
            from devteam.git._subprocess import GitError
            mock_git.side_effect = GitError(
                ["push", "origin", "--delete", "feat/gone"],
                1,
                "error: unable to delete 'feat/gone': remote ref does not exist",
            )
            # Should not raise
            delete_remote_branch(git_repo, "feat/gone")


class TestBranchQueries:
    def test_branch_exists_local_true(self, git_repo: Path):
        create_feature_branch(git_repo, "feat/exists")
        assert branch_exists_local(git_repo, "feat/exists") is True

    def test_branch_exists_local_false(self, git_repo: Path):
        assert branch_exists_local(git_repo, "feat/nope") is False

    def test_get_current_branch(self, git_repo: Path):
        branch = get_current_branch(git_repo)
        # Initial branch is main or master depending on git config
        assert branch in ("main", "master")

    def test_get_default_branch(self, git_repo: Path):
        branch = get_default_branch(git_repo)
        assert branch in ("main", "master")

    def test_branch_exists_remote_mocked(self, git_repo: Path):
        """branch_exists_remote calls ls-remote (mocked)."""
        with patch("devteam.git.branch.run_git") as mock_git:
            mock_git.return_value = "abc123\trefs/heads/feat/x"
            assert branch_exists_remote(git_repo, "feat/x") is True

        with patch("devteam.git.branch.run_git") as mock_git:
            mock_git.return_value = ""
            assert branch_exists_remote(git_repo, "feat/y") is False
```

Run:
```bash
pixi run pytest tests/git/test_branch.py -x
```
Expected: All tests fail (ImportError).

- [ ] **Step 2 (5 min):** Implement branch lifecycle.

`src/devteam/git/branch.py`:
```python
"""Branch lifecycle — create feature branches, delete local+remote.

All operations are idempotent: creating an existing branch or deleting
a non-existent one is a safe no-op.
"""

from __future__ import annotations

from pathlib import Path

from devteam.git._subprocess import GitError, run_git

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
    """
    if branch_exists_local(repo_root, branch):
        return
    run_git(["branch", branch, base_ref], cwd=repo_root)


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
        ValueError: If trying to delete a protected branch (main/master).
    """
    if branch in PROTECTED_BRANCHES:
        raise ValueError(f"Cannot delete default branch '{branch}'")

    if not branch_exists_local(repo_root, branch):
        return

    flag = "-D" if force else "-d"
    run_git(["branch", flag, branch], cwd=repo_root)


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
    """
    try:
        run_git(["push", remote, "--delete", branch], cwd=repo_root)
    except GitError as e:
        # Branch already deleted on remote — idempotent
        if "remote ref does not exist" in e.stderr:
            return
        raise


def branch_exists_local(repo_root: Path, branch: str) -> bool:
    """Check if a branch exists locally.

    Args:
        repo_root: Root of the git repo.
        branch: Branch name to check.

    Returns:
        True if the branch exists.
    """
    try:
        run_git(["rev-parse", "--verify", f"refs/heads/{branch}"], cwd=repo_root)
        return True
    except GitError:
        return False


def branch_exists_remote(
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
    result = run_git(
        ["ls-remote", "--heads", remote, f"refs/heads/{branch}"],
        cwd=repo_root,
    )
    return bool(result.strip())


def get_current_branch(repo_root: Path) -> str:
    """Get the current branch name.

    Args:
        repo_root: Root of the git repo.

    Returns:
        Current branch name (e.g. 'main', 'feat/login').
    """
    return run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)


def get_default_branch(repo_root: Path) -> str:
    """Get the default branch name (main or master).

    Checks local branches. Falls back to 'main' if neither exists.

    Args:
        repo_root: Root of the git repo.

    Returns:
        'main' or 'master'.
    """
    if branch_exists_local(repo_root, "main"):
        return "main"
    if branch_exists_local(repo_root, "master"):
        return "master"
    return "main"
```

Run:
```bash
pixi run pytest tests/git/test_branch.py -x -v
```
Expected: All 12 tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/devteam/git/branch.py tests/git/test_branch.py
git commit -m "feat: branch lifecycle with idempotent create/delete"
```

---

## Task 4: Fork detection and management

**Files:**
- Create: `src/devteam/git/fork.py`
- Create: `tests/git/test_fork.py`

- [ ] **Step 1 (3 min):** Write the failing tests.

`tests/git/test_fork.py`:
```python
"""Tests for fork detection and management."""

import json
from pathlib import Path
from unittest.mock import patch, call

import pytest

from devteam.git.fork import (
    ForkStatus,
    check_push_access,
    find_existing_fork,
    ensure_fork,
    setup_fork_remotes,
)


class TestCheckPushAccess:
    def test_has_push_access(self):
        """Returns True when user has push permissions."""
        with patch("devteam.git.fork.run_gh") as mock_gh:
            mock_gh.return_value = {"permissions": {"push": True}}
            assert check_push_access("org/repo") is True
            mock_gh.assert_called_once_with(
                ["api", "repos/org/repo", "--jq", ".permissions"],
                parse_json=True,
            )

    def test_no_push_access(self):
        """Returns False when user lacks push permissions."""
        with patch("devteam.git.fork.run_gh") as mock_gh:
            mock_gh.return_value = {"permissions": {"push": False}}
            assert check_push_access("org/repo") is False

    def test_api_error_returns_false(self):
        """Returns False on API errors (repo not found, etc)."""
        with patch("devteam.git.fork.run_gh") as mock_gh:
            from devteam.git._subprocess import GhError
            mock_gh.side_effect = GhError(["api"], 1, "Not Found")
            assert check_push_access("org/private-repo") is False


class TestFindExistingFork:
    def test_finds_fork(self):
        """Finds a fork when one exists."""
        with patch("devteam.git.fork.run_gh") as mock_gh:
            mock_gh.return_value = [
                {"nameWithOwner": "myuser/repo", "parent": {"nameWithOwner": "org/repo"}},
                {"nameWithOwner": "myuser/other", "parent": {"nameWithOwner": "org/other"}},
            ]
            result = find_existing_fork("org/repo")
            assert result == "myuser/repo"

    def test_no_fork_found(self):
        """Returns None when no fork exists."""
        with patch("devteam.git.fork.run_gh") as mock_gh:
            mock_gh.return_value = []
            result = find_existing_fork("org/repo")
            assert result is None


class TestEnsureFork:
    def test_has_push_access(self):
        """Returns DIRECT when push access exists."""
        with patch("devteam.git.fork.check_push_access", return_value=True):
            status = ensure_fork("org/repo")
            assert status == ForkStatus.DIRECT

    def test_existing_fork(self):
        """Returns EXISTING_FORK when a fork is found."""
        with patch("devteam.git.fork.check_push_access", return_value=False):
            with patch("devteam.git.fork.find_existing_fork", return_value="myuser/repo"):
                status = ensure_fork("org/repo")
                assert status == ForkStatus.EXISTING_FORK

    def test_creates_new_fork(self):
        """Creates a fork when none exists and returns NEW_FORK."""
        with patch("devteam.git.fork.check_push_access", return_value=False):
            with patch("devteam.git.fork.find_existing_fork", return_value=None):
                with patch("devteam.git.fork.run_gh") as mock_gh:
                    status = ensure_fork("org/repo")
                    assert status == ForkStatus.NEW_FORK
                    mock_gh.assert_called_once_with(
                        ["repo", "fork", "org/repo", "--clone=false"],
                    )


class TestSetupForkRemotes:
    def test_setup_remotes(self, tmp_path: Path):
        """Configures origin as fork, upstream as original."""
        with patch("devteam.git.fork.run_git") as mock_git:
            setup_fork_remotes(tmp_path, "org/repo", "myuser/repo")
            calls = mock_git.call_args_list
            # Should set origin to the fork and upstream to original
            assert any(
                "set-url" in str(c) and "myuser/repo" in str(c) for c in calls
            )
            assert any(
                "upstream" in str(c) and "org/repo" in str(c) for c in calls
            )
```

Run:
```bash
pixi run pytest tests/git/test_fork.py -x
```
Expected: All tests fail (ImportError).

- [ ] **Step 2 (5 min):** Implement fork detection.

`src/devteam/git/fork.py`:
```python
"""Fork detection and management for open-source contributions.

Handles the three access scenarios:
1. DIRECT — user has push access (collaborator or own repo).
2. EXISTING_FORK — user already forked the repo.
3. NEW_FORK — auto-fork via `gh repo fork`.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from devteam.git._subprocess import GhError, run_gh, run_git


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
    """
    try:
        permissions = run_gh(
            ["api", f"repos/{repo_nwo}", "--jq", ".permissions"],
            parse_json=True,
        )
        return permissions.get("push", False)
    except GhError:
        return False


def find_existing_fork(upstream_nwo: str) -> str | None:
    """Check if the authenticated user already has a fork of the repo.

    Args:
        upstream_nwo: Upstream repo in 'owner/name' format.

    Returns:
        The 'owner/name' of the existing fork, or None.
    """
    try:
        repos = run_gh(
            [
                "repo", "list",
                "--fork",
                "--json", "nameWithOwner,parent",
                "--limit", "200",
            ],
            parse_json=True,
        )
    except GhError:
        return None

    for repo in repos:
        parent = repo.get("parent", {})
        if parent.get("nameWithOwner") == upstream_nwo:
            return repo["nameWithOwner"]
    return None


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
    """
    if check_push_access(upstream_nwo):
        return ForkStatus.DIRECT

    existing = find_existing_fork(upstream_nwo)
    if existing is not None:
        return ForkStatus.EXISTING_FORK

    run_gh(["repo", "fork", upstream_nwo, "--clone=false"])
    return ForkStatus.NEW_FORK


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
    """
    fork_url = f"https://github.com/{fork_nwo}.git"
    upstream_url = f"https://github.com/{upstream_nwo}.git"

    # Set origin to the fork
    try:
        run_git(["remote", "set-url", "origin", fork_url], cwd=repo_root)
    except Exception:
        run_git(["remote", "add", "origin", fork_url], cwd=repo_root)

    # Set upstream to the original repo
    try:
        run_git(["remote", "set-url", "upstream", upstream_url], cwd=repo_root)
    except Exception:
        run_git(["remote", "add", "upstream", upstream_url], cwd=repo_root)
```

Run:
```bash
pixi run pytest tests/git/test_fork.py -x -v
```
Expected: All 9 tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/devteam/git/fork.py tests/git/test_fork.py
git commit -m "feat: fork detection with push access check, existing fork lookup, auto-fork"
```

---

## Task 5: PR creation and status checking

**Files:**
- Create: `src/devteam/git/pr.py`
- Create: `tests/git/test_pr.py`

- [ ] **Step 1 (4 min):** Write the failing tests.

`tests/git/test_pr.py`:
```python
"""Tests for PR creation, status checking, and merge."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from devteam.git.pr import (
    PRInfo,
    PRCheckStatus,
    PRFeedback,
    CodeRabbitCategory,
    create_pr,
    check_pr_status,
    merge_pr,
    close_pr,
    find_existing_pr,
    categorize_coderabbit_comments,
)


class TestCreatePR:
    def test_create_pr_basic(self, tmp_path: Path):
        """Creates a PR via gh CLI."""
        with patch("devteam.git.pr.find_existing_pr", return_value=None):
            with patch("devteam.git.pr.run_gh") as mock_gh:
                mock_gh.return_value = {
                    "number": 42,
                    "url": "https://github.com/org/repo/pull/42",
                    "headRefName": "feat/login",
                }
                info = create_pr(
                    cwd=tmp_path,
                    title="Add login flow",
                    body="Implements user authentication",
                    branch="feat/login",
                    base="main",
                )
                assert info.number == 42
                assert info.url == "https://github.com/org/repo/pull/42"

    def test_create_pr_idempotent(self, tmp_path: Path):
        """If a PR already exists for the branch, returns it."""
        existing = PRInfo(
            number=10,
            url="https://github.com/org/repo/pull/10",
            branch="feat/login",
        )
        with patch("devteam.git.pr.find_existing_pr", return_value=existing):
            info = create_pr(
                cwd=tmp_path,
                title="Add login flow",
                body="...",
                branch="feat/login",
                base="main",
            )
            assert info.number == 10

    def test_create_pr_from_fork(self, tmp_path: Path):
        """Creates a PR targeting upstream repo from a fork."""
        with patch("devteam.git.pr.find_existing_pr", return_value=None):
            with patch("devteam.git.pr.run_gh") as mock_gh:
                mock_gh.return_value = {
                    "number": 99,
                    "url": "https://github.com/org/repo/pull/99",
                    "headRefName": "feat/fix",
                }
                info = create_pr(
                    cwd=tmp_path,
                    title="Fix bug",
                    body="...",
                    branch="feat/fix",
                    base="main",
                    upstream_repo="org/repo",
                )
                # Should pass --repo to gh
                call_args = mock_gh.call_args
                assert "--repo" in str(call_args) or "org/repo" in str(call_args)


class TestFindExistingPR:
    def test_finds_pr(self, tmp_path: Path):
        """Finds existing PR for a branch."""
        with patch("devteam.git.pr.run_gh") as mock_gh:
            mock_gh.return_value = [
                {
                    "number": 5,
                    "url": "https://github.com/org/repo/pull/5",
                    "headRefName": "feat/login",
                    "state": "OPEN",
                }
            ]
            result = find_existing_pr(tmp_path, "feat/login")
            assert result is not None
            assert result.number == 5

    def test_no_pr_found(self, tmp_path: Path):
        """Returns None when no PR exists."""
        with patch("devteam.git.pr.run_gh") as mock_gh:
            mock_gh.return_value = []
            result = find_existing_pr(tmp_path, "feat/nope")
            assert result is None


class TestCheckPRStatus:
    def test_all_green(self, tmp_path: Path):
        """All CI checks pass, no review comments."""
        with patch("devteam.git.pr.run_gh") as mock_gh:
            # First call: checks, second call: reviews
            mock_gh.side_effect = [
                [
                    {"name": "ci", "state": "completed", "conclusion": "success"},
                    {"name": "lint", "state": "completed", "conclusion": "success"},
                ],
                {
                    "reviews": [],
                    "comments": [],
                    "reviewDecision": "APPROVED",
                },
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.ci_complete is True
            assert feedback.all_green is True
            assert feedback.check_status == PRCheckStatus.ALL_PASSED

    def test_ci_pending(self, tmp_path: Path):
        """CI checks still running."""
        with patch("devteam.git.pr.run_gh") as mock_gh:
            mock_gh.side_effect = [
                [
                    {"name": "ci", "state": "in_progress", "conclusion": None},
                ],
                {"reviews": [], "comments": [], "reviewDecision": ""},
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.ci_complete is False
            assert feedback.all_green is False

    def test_ci_failed(self, tmp_path: Path):
        """CI check failed."""
        with patch("devteam.git.pr.run_gh") as mock_gh:
            mock_gh.side_effect = [
                [
                    {"name": "ci", "state": "completed", "conclusion": "failure"},
                ],
                {"reviews": [], "comments": [], "reviewDecision": ""},
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.ci_complete is True
            assert feedback.check_status == PRCheckStatus.SOME_FAILED

    def test_no_checks(self, tmp_path: Path):
        """Repo with no CI checks configured."""
        with patch("devteam.git.pr.run_gh") as mock_gh:
            mock_gh.side_effect = [
                [],  # no checks
                {"reviews": [], "comments": [], "reviewDecision": ""},
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.ci_complete is True
            assert feedback.check_status == PRCheckStatus.NO_CHECKS


class TestMergePR:
    def test_merge_squash(self, tmp_path: Path):
        """Merges a PR via squash merge."""
        with patch("devteam.git.pr.run_gh") as mock_gh:
            merge_pr(tmp_path, 42, strategy="squash")
            mock_gh.assert_called_once()
            call_str = str(mock_gh.call_args)
            assert "merge" in call_str
            assert "--squash" in call_str

    def test_merge_already_merged(self, tmp_path: Path):
        """Merging an already-merged PR is a no-op."""
        with patch("devteam.git.pr.run_gh") as mock_gh:
            from devteam.git._subprocess import GhError
            mock_gh.side_effect = GhError(
                ["pr", "merge"], 1, "already been merged"
            )
            # Should not raise
            merge_pr(tmp_path, 42)


class TestClosePR:
    def test_close_pr(self, tmp_path: Path):
        """Closes a PR with a comment."""
        with patch("devteam.git.pr.run_gh") as mock_gh:
            close_pr(tmp_path, 42, comment="Cancelled by operator")
            assert mock_gh.call_count >= 1

    def test_close_already_closed(self, tmp_path: Path):
        """Closing an already-closed PR is a no-op."""
        with patch("devteam.git.pr.run_gh") as mock_gh:
            from devteam.git._subprocess import GhError
            mock_gh.side_effect = GhError(
                ["pr", "close"], 1, "already closed"
            )
            # Should not raise
            close_pr(tmp_path, 42)


class TestCodeRabbitCategorization:
    def test_categorize_comments(self):
        """CodeRabbit comments are sorted by severity."""
        comments = [
            {"body": "[nitpick] rename variable", "author": "coderabbitai[bot]"},
            {"body": "[error] SQL injection vulnerability", "author": "coderabbitai[bot]"},
            {"body": "[warning] missing null check", "author": "coderabbitai[bot]"},
            {"body": "looks good", "author": "human-reviewer"},
        ]
        categorized = categorize_coderabbit_comments(comments)
        assert len(categorized.errors) == 1
        assert len(categorized.warnings) == 1
        assert len(categorized.nitpicks) == 1
        assert "SQL injection" in categorized.errors[0]

    def test_empty_comments(self):
        """No CodeRabbit comments returns empty categories."""
        categorized = categorize_coderabbit_comments([])
        assert len(categorized.errors) == 0
        assert len(categorized.warnings) == 0
        assert len(categorized.nitpicks) == 0
```

Run:
```bash
pixi run pytest tests/git/test_pr.py -x
```
Expected: All tests fail (ImportError).

- [ ] **Step 2 (5 min):** Implement PR creation and status checking.

`src/devteam/git/pr.py`:
```python
"""PR creation, status checking, merge, and feedback handling.

All operations are idempotent: creating a PR that already exists returns
the existing one, merging an already-merged PR is a no-op, etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from devteam.git._subprocess import GhError, run_gh


@dataclass
class PRInfo:
    """Basic information about a pull request."""

    number: int
    url: str
    branch: str


class PRCheckStatus(Enum):
    """Aggregate status of CI checks on a PR."""

    ALL_PASSED = "all_passed"
    SOME_FAILED = "some_failed"
    PENDING = "pending"
    NO_CHECKS = "no_checks"


class CodeRabbitCategory(Enum):
    """Severity category for CodeRabbit comments."""

    ERROR = "error"
    WARNING = "warning"
    NITPICK = "nitpick"
    OTHER = "other"


@dataclass
class CategorizedComments:
    """CodeRabbit comments sorted by severity."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    nitpicks: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)


@dataclass
class PRFeedback:
    """Result of checking PR status — CI, reviews, comments."""

    ci_complete: bool
    check_status: PRCheckStatus
    all_green: bool
    failed_checks: list[str] = field(default_factory=list)
    review_comments: list[dict[str, Any]] = field(default_factory=list)
    review_decision: str = ""
    coderabbit_comments: CategorizedComments = field(
        default_factory=CategorizedComments
    )


def find_existing_pr(
    cwd: Path,
    branch: str,
    repo: str | None = None,
) -> PRInfo | None:
    """Check if a PR already exists for the given branch.

    Idempotent recovery: before creating a new PR, check if one exists.

    Args:
        cwd: Working directory (must be in a git repo).
        branch: Head branch name.
        repo: Optional upstream repo in 'owner/name' format.

    Returns:
        PRInfo if a PR exists, None otherwise.
    """
    args = [
        "pr", "list",
        "--head", branch,
        "--state", "open",
        "--json", "number,url,headRefName,state",
    ]
    if repo:
        args.extend(["--repo", repo])

    try:
        prs = run_gh(args, cwd=cwd, parse_json=True)
    except GhError:
        return None

    if not prs:
        return None

    pr = prs[0]
    return PRInfo(
        number=pr["number"],
        url=pr["url"],
        branch=pr["headRefName"],
    )


def create_pr(
    cwd: Path,
    title: str,
    body: str,
    branch: str,
    base: str = "main",
    upstream_repo: str | None = None,
) -> PRInfo:
    """Create a pull request via gh CLI.

    Idempotent: if a PR already exists for the branch, returns it.

    Args:
        cwd: Working directory (must be in a git repo).
        title: PR title.
        body: PR body/description.
        branch: Head branch name.
        base: Base branch to merge into.
        upstream_repo: If working from a fork, the upstream 'owner/name'.

    Returns:
        PRInfo with number and URL.
    """
    # Idempotency: check if PR already exists
    existing = find_existing_pr(cwd, branch, repo=upstream_repo)
    if existing is not None:
        return existing

    args = [
        "pr", "create",
        "--title", title,
        "--body", body,
        "--head", branch,
        "--base", base,
    ]
    if upstream_repo:
        args.extend(["--repo", upstream_repo])

    result = run_gh(args, cwd=cwd, parse_json=True)
    return PRInfo(
        number=result["number"],
        url=result["url"],
        branch=result["headRefName"],
    )


def check_pr_status(cwd: Path, pr_number: int) -> PRFeedback:
    """Check CI checks and review status for a PR.

    This is a single-shot check (no polling loop). The workflow
    layer calls this repeatedly with DBOS.sleep() between calls.

    Args:
        cwd: Working directory (must be in a git repo).
        pr_number: PR number to check.

    Returns:
        PRFeedback with CI status, review comments, and categorized
        CodeRabbit comments.
    """
    # Get CI check status
    try:
        checks = run_gh(
            ["pr", "checks", str(pr_number), "--json", "name,state,conclusion"],
            cwd=cwd,
            parse_json=True,
        )
    except GhError:
        checks = []

    # Get review status
    try:
        review_data = run_gh(
            [
                "pr", "view", str(pr_number),
                "--json", "reviews,comments,reviewDecision",
            ],
            cwd=cwd,
            parse_json=True,
        )
    except GhError:
        review_data = {"reviews": [], "comments": [], "reviewDecision": ""}

    # Analyze CI checks
    if not checks:
        ci_complete = True
        check_status = PRCheckStatus.NO_CHECKS
        failed_checks = []
    else:
        all_completed = all(c.get("state") == "completed" for c in checks)
        ci_complete = all_completed
        failed = [
            c["name"] for c in checks
            if c.get("conclusion") == "failure"
        ]
        failed_checks = failed

        if not all_completed:
            check_status = PRCheckStatus.PENDING
        elif failed:
            check_status = PRCheckStatus.SOME_FAILED
        else:
            check_status = PRCheckStatus.ALL_PASSED

    # Categorize CodeRabbit comments
    all_comments = review_data.get("comments", [])
    coderabbit = categorize_coderabbit_comments(all_comments)

    review_decision = review_data.get("reviewDecision", "")
    all_green = (
        check_status in (PRCheckStatus.ALL_PASSED, PRCheckStatus.NO_CHECKS)
        and not coderabbit.errors
        and review_decision in ("APPROVED", "")
    )

    return PRFeedback(
        ci_complete=ci_complete,
        check_status=check_status,
        all_green=all_green,
        failed_checks=failed_checks,
        review_comments=review_data.get("reviews", []),
        review_decision=review_decision,
        coderabbit_comments=coderabbit,
    )


def merge_pr(
    cwd: Path,
    pr_number: int,
    strategy: str = "squash",
) -> None:
    """Merge a PR via gh CLI.

    Idempotent: if the PR is already merged, this is a no-op.

    Args:
        cwd: Working directory.
        pr_number: PR number to merge.
        strategy: Merge strategy — 'squash', 'merge', or 'rebase'.
    """
    strategy_flag = f"--{strategy}"
    try:
        run_gh(
            ["pr", "merge", str(pr_number), strategy_flag, "--auto", "--delete-branch"],
            cwd=cwd,
        )
    except GhError as e:
        if "already been merged" in e.stderr:
            return
        raise


def close_pr(
    cwd: Path,
    pr_number: int,
    comment: str | None = None,
) -> None:
    """Close a PR without merging.

    Idempotent: closing an already-closed PR is a no-op.

    Args:
        cwd: Working directory.
        pr_number: PR number to close.
        comment: Optional comment to post before closing.
    """
    if comment:
        try:
            run_gh(
                ["pr", "comment", str(pr_number), "--body", comment],
                cwd=cwd,
            )
        except GhError:
            pass  # Comment failure is non-fatal

    try:
        run_gh(["pr", "close", str(pr_number)], cwd=cwd)
    except GhError as e:
        if "already closed" in e.stderr:
            return
        raise


def categorize_coderabbit_comments(
    comments: list[dict[str, Any]],
) -> CategorizedComments:
    """Categorize CodeRabbit comments by severity.

    Errors first, warnings second, nitpicks deprioritized.

    Args:
        comments: List of comment dicts with 'body' and 'author' keys.

    Returns:
        CategorizedComments with sorted lists.
    """
    result = CategorizedComments()

    for comment in comments:
        author = comment.get("author", "")
        # Handle author as string or dict
        if isinstance(author, dict):
            author = author.get("login", "")
        if "coderabbit" not in author.lower():
            continue

        body = comment.get("body", "")
        body_lower = body.lower()

        if body_lower.startswith("[error]") or "[error]" in body_lower:
            result.errors.append(body)
        elif body_lower.startswith("[warning]") or "[warning]" in body_lower:
            result.warnings.append(body)
        elif body_lower.startswith("[nitpick]") or "[nitpick]" in body_lower:
            result.nitpicks.append(body)
        else:
            result.other.append(body)

    return result
```

Run:
```bash
pixi run pytest tests/git/test_pr.py -x -v
```
Expected: All 14 tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/devteam/git/pr.py tests/git/test_pr.py
git commit -m "feat: PR create/check/merge/close with CodeRabbit categorization"
```

---

## Task 6: PR feedback loop with session resumption and circuit breaker

**Files:**
- Create: `src/devteam/git/pr_feedback.py`
- Create: `tests/git/test_pr_feedback.py`

- [ ] **Step 1 (3 min):** Write the failing tests.

`tests/git/test_pr_feedback.py`:
```python
"""Tests for PR feedback loop logic."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from devteam.git.pr_feedback import (
    FeedbackIteration,
    FeedbackLoopConfig,
    FeedbackLoopResult,
    FeedbackLoopOutcome,
    build_feedback_prompt,
    filter_new_feedback,
    should_continue_loop,
)
from devteam.git.pr import (
    PRFeedback,
    PRCheckStatus,
    CategorizedComments,
)


class TestBuildFeedbackPrompt:
    def test_prompt_includes_failed_checks(self):
        """Prompt mentions which CI checks failed."""
        feedback = PRFeedback(
            ci_complete=True,
            check_status=PRCheckStatus.SOME_FAILED,
            all_green=False,
            failed_checks=["lint", "test"],
        )
        prompt = build_feedback_prompt(feedback, iteration=1, max_iterations=5)
        assert "lint" in prompt
        assert "test" in prompt

    def test_prompt_prioritizes_errors(self):
        """CodeRabbit errors appear before warnings and nitpicks."""
        coderabbit = CategorizedComments(
            errors=["[error] SQL injection"],
            warnings=["[warning] missing check"],
            nitpicks=["[nitpick] rename var"],
        )
        feedback = PRFeedback(
            ci_complete=True,
            check_status=PRCheckStatus.ALL_PASSED,
            all_green=False,
            coderabbit_comments=coderabbit,
        )
        prompt = build_feedback_prompt(feedback, iteration=1, max_iterations=5)
        error_pos = prompt.find("SQL injection")
        warning_pos = prompt.find("missing check")
        nitpick_pos = prompt.find("rename var")
        assert error_pos < warning_pos < nitpick_pos

    def test_prompt_includes_iteration_count(self):
        """Prompt shows current iteration and max."""
        feedback = PRFeedback(
            ci_complete=True,
            check_status=PRCheckStatus.SOME_FAILED,
            all_green=False,
            failed_checks=["test"],
        )
        prompt = build_feedback_prompt(feedback, iteration=3, max_iterations=5)
        assert "3" in prompt
        assert "5" in prompt


class TestFilterNewFeedback:
    def test_filters_by_timestamp(self):
        """Only includes comments newer than the cutoff."""
        comments = [
            {
                "body": "old comment",
                "createdAt": "2026-03-20T10:00:00Z",
                "author": "coderabbitai[bot]",
            },
            {
                "body": "[error] new issue",
                "createdAt": "2026-03-25T10:00:00Z",
                "author": "coderabbitai[bot]",
            },
        ]
        cutoff = datetime(2026, 3, 24, tzinfo=timezone.utc)
        filtered = filter_new_feedback(comments, since=cutoff)
        assert len(filtered) == 1
        assert "new issue" in filtered[0]["body"]

    def test_no_cutoff_returns_all(self):
        """Without a cutoff, returns all comments."""
        comments = [{"body": "a"}, {"body": "b"}]
        filtered = filter_new_feedback(comments, since=None)
        assert len(filtered) == 2


class TestShouldContinueLoop:
    def test_continue_when_not_green(self):
        """Continue if feedback is not all green and under max iterations."""
        config = FeedbackLoopConfig(max_iterations=5)
        result = should_continue_loop(
            iteration=2,
            all_green=False,
            config=config,
        )
        assert result is True

    def test_stop_when_all_green(self):
        """Stop when all checks pass."""
        config = FeedbackLoopConfig(max_iterations=5)
        result = should_continue_loop(
            iteration=2,
            all_green=True,
            config=config,
        )
        assert result is False

    def test_circuit_breaker(self):
        """Stop at max iterations (circuit breaker)."""
        config = FeedbackLoopConfig(max_iterations=5)
        result = should_continue_loop(
            iteration=5,
            all_green=False,
            config=config,
        )
        assert result is False

    def test_custom_max_iterations(self):
        """Config controls the circuit breaker threshold."""
        config = FeedbackLoopConfig(max_iterations=3)
        assert should_continue_loop(3, False, config) is False
        assert should_continue_loop(2, False, config) is True
```

Run:
```bash
pixi run pytest tests/git/test_pr_feedback.py -x
```
Expected: All tests fail (ImportError).

- [ ] **Step 2 (5 min):** Implement PR feedback loop logic.

`src/devteam/git/pr_feedback.py`:
```python
"""PR feedback loop — session resumption, diff-only feedback, circuit breaker.

This module contains the pure logic for the feedback loop. The actual
DBOS workflow orchestration (polling, sleep, agent invocation) lives in
the workflow layer — this module provides the building blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from devteam.git.pr import CategorizedComments, PRFeedback


class FeedbackLoopOutcome(Enum):
    """Result of the entire feedback loop."""

    ALL_GREEN = "all_green"
    CIRCUIT_BREAKER = "circuit_breaker"
    ESCALATED = "escalated"


@dataclass
class FeedbackLoopConfig:
    """Configuration for the PR feedback loop."""

    max_iterations: int = 5
    poll_interval_seconds: int = 60


@dataclass
class FeedbackIteration:
    """Record of one feedback-fix iteration."""

    iteration: int
    feedback: PRFeedback
    session_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FeedbackLoopResult:
    """Final result of the feedback loop."""

    outcome: FeedbackLoopOutcome
    iterations: list[FeedbackIteration] = field(default_factory=list)
    total_iterations: int = 0


def build_feedback_prompt(
    feedback: PRFeedback,
    iteration: int,
    max_iterations: int,
) -> str:
    """Build a prompt for the engineer to fix PR feedback.

    Structures feedback by priority: errors > warnings > nitpicks.
    Includes iteration count so the agent knows how many attempts remain.

    Args:
        feedback: Current PR feedback state.
        iteration: Current iteration number (1-based).
        max_iterations: Maximum allowed iterations.

    Returns:
        Formatted prompt string for the agent.
    """
    sections: list[str] = []

    sections.append(
        f"## PR Fix Iteration {iteration}/{max_iterations}\n"
        f"This is fix attempt {iteration} of {max_iterations}. "
        f"Focus on the highest-severity issues first."
    )

    # Failed CI checks
    if feedback.failed_checks:
        checks_str = "\n".join(f"  - {c}" for c in feedback.failed_checks)
        sections.append(f"### Failed CI Checks\n{checks_str}")

    # CodeRabbit comments — errors first
    cr = feedback.coderabbit_comments
    if cr.errors:
        errors_str = "\n".join(f"  - {e}" for e in cr.errors)
        sections.append(f"### CodeRabbit Errors (must fix)\n{errors_str}")

    if cr.warnings:
        warnings_str = "\n".join(f"  - {w}" for w in cr.warnings)
        sections.append(f"### CodeRabbit Warnings (should fix)\n{warnings_str}")

    if cr.nitpicks:
        nitpicks_str = "\n".join(f"  - {n}" for n in cr.nitpicks)
        sections.append(f"### CodeRabbit Nitpicks (low priority)\n{nitpicks_str}")

    # Review comments from humans
    if feedback.review_comments:
        comments_str = "\n".join(
            f"  - {r.get('body', str(r))}" for r in feedback.review_comments
        )
        sections.append(f"### Review Comments\n{comments_str}")

    return "\n\n".join(sections)


def filter_new_feedback(
    comments: list[dict[str, Any]],
    since: datetime | None,
) -> list[dict[str, Any]]:
    """Filter comments to only those newer than a cutoff.

    Enables diff-only feedback: each iteration only shows NEW
    failures and comments, not everything from the beginning.

    Args:
        comments: List of comment dicts with optional 'createdAt' key.
        since: Only include comments after this timestamp. If None,
               return all comments.

    Returns:
        Filtered list of comments.
    """
    if since is None:
        return comments

    result = []
    for comment in comments:
        created_str = comment.get("createdAt")
        if created_str is None:
            result.append(comment)
            continue
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if created > since:
                result.append(comment)
        except (ValueError, AttributeError):
            result.append(comment)
    return result


def should_continue_loop(
    iteration: int,
    all_green: bool,
    config: FeedbackLoopConfig,
) -> bool:
    """Decide whether to continue the feedback loop.

    Args:
        iteration: Current iteration number (1-based).
        all_green: Whether all checks and reviews pass.
        config: Loop configuration (max iterations, etc.).

    Returns:
        True if the loop should continue (more fixes needed),
        False if it should stop (success or circuit breaker).
    """
    if all_green:
        return False
    if iteration >= config.max_iterations:
        return False
    return True
```

Run:
```bash
pixi run pytest tests/git/test_pr_feedback.py -x -v
```
Expected: All 8 tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/devteam/git/pr_feedback.py tests/git/test_pr_feedback.py
git commit -m "feat: PR feedback loop logic with circuit breaker and diff-only filtering"
```

---

## Task 7: Cleanup after merge and cancel

**Files:**
- Create: `src/devteam/git/cleanup.py`
- Create: `tests/git/test_cleanup.py`

- [ ] **Step 1 (3 min):** Write the failing tests.

`tests/git/test_cleanup.py`:
```python
"""Tests for cleanup operations after merge and cancel."""

import subprocess
from pathlib import Path
from unittest.mock import patch, call, MagicMock

import pytest

from devteam.git.cleanup import (
    CleanupResult,
    CleanupAction,
    cleanup_after_merge,
    cleanup_on_cancel,
    cleanup_single_pr,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    readme = repo / "README.md"
    readme.write_text("# Test")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


class TestCleanupAfterMerge:
    def test_full_cleanup(self, git_repo: Path):
        """Cleans up worktree, local branch, and remote branch after merge."""
        from devteam.git.worktree import create_worktree
        info = create_worktree(git_repo, "feat/merged")

        with patch("devteam.git.cleanup.delete_remote_branch") as mock_remote:
            result = cleanup_after_merge(
                repo_root=git_repo,
                branch="feat/merged",
                worktree_path=info.path,
            )
            assert CleanupAction.WORKTREE_REMOVED in result.actions
            assert CleanupAction.LOCAL_BRANCH_DELETED in result.actions
            assert CleanupAction.REMOTE_BRANCH_DELETED in result.actions
            assert not info.path.exists()
            mock_remote.assert_called_once()

    def test_cleanup_idempotent(self, git_repo: Path):
        """Running cleanup twice does not raise."""
        with patch("devteam.git.cleanup.delete_remote_branch"):
            result1 = cleanup_after_merge(
                repo_root=git_repo,
                branch="feat/already-gone",
                worktree_path=git_repo / ".worktrees" / "feat-already-gone",
            )
            # All actions should still be reported (idempotent path)
            assert result1.success is True


class TestCleanupOnCancel:
    def test_cancel_closes_prs_and_cleans(self, git_repo: Path):
        """Cancel closes open PRs, deletes branches, removes worktrees."""
        from devteam.git.worktree import create_worktree
        wt1 = create_worktree(git_repo, "feat/cancel-a")
        wt2 = create_worktree(git_repo, "feat/cancel-b")

        pr_branches = [
            {"branch": "feat/cancel-a", "pr_number": 12, "worktree_path": wt1.path, "merged": False},
            {"branch": "feat/cancel-b", "pr_number": 14, "worktree_path": wt2.path, "merged": False},
        ]

        with patch("devteam.git.cleanup.close_pr") as mock_close:
            with patch("devteam.git.cleanup.delete_remote_branch"):
                result = cleanup_on_cancel(
                    repo_root=git_repo,
                    pr_branches=pr_branches,
                )
                assert result.success is True
                assert mock_close.call_count == 2
                assert not wt1.path.exists()
                assert not wt2.path.exists()

    def test_cancel_preserves_merged_prs(self, git_repo: Path):
        """Already-merged PRs are preserved on cancel."""
        pr_branches = [
            {"branch": "feat/merged", "pr_number": 11, "worktree_path": None, "merged": True},
            {"branch": "feat/open", "pr_number": 12, "worktree_path": None, "merged": False},
        ]

        with patch("devteam.git.cleanup.close_pr") as mock_close:
            with patch("devteam.git.cleanup.delete_remote_branch"):
                with patch("devteam.git.cleanup.remove_worktree"):
                    with patch("devteam.git.cleanup.delete_local_branch"):
                        result = cleanup_on_cancel(
                            repo_root=git_repo,
                            pr_branches=pr_branches,
                        )
                        # Only the open PR should be closed
                        mock_close.assert_called_once()
                        assert len(result.preserved) == 1
                        assert result.preserved[0]["pr_number"] == 11

    def test_cancel_idempotent(self, git_repo: Path):
        """Running cancel twice is safe."""
        pr_branches = [
            {"branch": "feat/gone", "pr_number": 99, "worktree_path": None, "merged": False},
        ]

        with patch("devteam.git.cleanup.close_pr"):
            with patch("devteam.git.cleanup.delete_remote_branch"):
                with patch("devteam.git.cleanup.remove_worktree"):
                    with patch("devteam.git.cleanup.delete_local_branch"):
                        result1 = cleanup_on_cancel(repo_root=git_repo, pr_branches=pr_branches)
                        result2 = cleanup_on_cancel(repo_root=git_repo, pr_branches=pr_branches)
                        assert result1.success is True
                        assert result2.success is True


class TestCleanupSinglePR:
    def test_cleanup_single(self, git_repo: Path):
        """Cleans up a single PR's artifacts."""
        with patch("devteam.git.cleanup.close_pr"):
            with patch("devteam.git.cleanup.delete_remote_branch"):
                with patch("devteam.git.cleanup.remove_worktree"):
                    with patch("devteam.git.cleanup.delete_local_branch"):
                        result = cleanup_single_pr(
                            repo_root=git_repo,
                            branch="feat/single",
                            pr_number=5,
                            worktree_path=None,
                        )
                        assert result.success is True
```

Run:
```bash
pixi run pytest tests/git/test_cleanup.py -x
```
Expected: All tests fail (ImportError).

- [ ] **Step 2 (5 min):** Implement cleanup operations.

`src/devteam/git/cleanup.py`:
```python
"""Cleanup operations after merge and on cancel.

Post-merge: remove worktree, delete local branch, delete remote branch.
On cancel: close PRs, then full cleanup for each. Preserves merged PRs.

All operations are idempotent — safe to run multiple times.
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

    # 1. Remove worktree
    if worktree_path is not None:
        try:
            remove_worktree(repo_root, worktree_path, force=True)
            result.actions.append(CleanupAction.WORKTREE_REMOVED)
        except Exception as e:
            result.errors.append(f"Failed to remove worktree: {e}")

    # 2. Delete local branch
    try:
        delete_local_branch(repo_root, branch, force=True)
        result.actions.append(CleanupAction.LOCAL_BRANCH_DELETED)
    except ValueError:
        # Protected branch — skip
        pass
    except Exception as e:
        result.errors.append(f"Failed to delete local branch: {e}")

    # 3. Delete remote branch
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

        if merged:
            combined.preserved.append(entry)
            continue

        if isinstance(worktree_path, str):
            worktree_path = Path(worktree_path)

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
```

Run:
```bash
pixi run pytest tests/git/test_cleanup.py -x -v
```
Expected: All 6 tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/devteam/git/cleanup.py tests/git/test_cleanup.py
git commit -m "feat: cleanup after merge and on cancel with preserved-merged tracking"
```

---

## Task 8: Idempotent recovery checks

**Files:**
- Create: `src/devteam/git/recovery.py`
- Create: `tests/git/test_recovery.py`

- [ ] **Step 1 (3 min):** Write the failing tests.

`tests/git/test_recovery.py`:
```python
"""Tests for idempotent recovery checks.

Pattern: before any side-effecting step, check if the effect
already happened. Every external action is idempotent on retry.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from devteam.git.recovery import (
    RecoveryCheck,
    check_worktree_state,
    check_branch_pushed,
    check_pr_exists,
    check_pr_merged,
    reset_worktree_to_clean,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    readme = repo / "README.md"
    readme.write_text("# Test")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


class TestCheckWorktreeState:
    def test_clean_worktree(self, git_repo: Path):
        """Clean worktree returns CLEAN status."""
        check = check_worktree_state(git_repo)
        assert check.clean is True

    def test_dirty_worktree(self, git_repo: Path):
        """Worktree with uncommitted changes returns DIRTY."""
        (git_repo / "dirty.txt").write_text("uncommitted")
        check = check_worktree_state(git_repo)
        assert check.clean is False
        assert "dirty.txt" in check.details

    def test_nonexistent_path(self, tmp_path: Path):
        """Non-existent path returns appropriate status."""
        fake = tmp_path / "nope"
        check = check_worktree_state(fake)
        assert check.exists is False


class TestCheckBranchPushed:
    def test_branch_on_remote(self, git_repo: Path):
        """Returns True if branch exists on remote with expected commits."""
        with patch("devteam.git.recovery.branch_exists_remote", return_value=True):
            assert check_branch_pushed(git_repo, "feat/x") is True

    def test_branch_not_on_remote(self, git_repo: Path):
        """Returns False if branch does not exist on remote."""
        with patch("devteam.git.recovery.branch_exists_remote", return_value=False):
            assert check_branch_pushed(git_repo, "feat/y") is False


class TestCheckPRExists:
    def test_pr_exists(self, tmp_path: Path):
        """Returns PR number if PR exists for branch."""
        with patch("devteam.git.recovery.find_existing_pr") as mock_find:
            from devteam.git.pr import PRInfo
            mock_find.return_value = PRInfo(number=42, url="...", branch="feat/x")
            result = check_pr_exists(tmp_path, "feat/x")
            assert result == 42

    def test_pr_does_not_exist(self, tmp_path: Path):
        """Returns None if no PR exists."""
        with patch("devteam.git.recovery.find_existing_pr", return_value=None):
            result = check_pr_exists(tmp_path, "feat/y")
            assert result is None


class TestCheckPRMerged:
    def test_pr_is_merged(self, tmp_path: Path):
        """Returns True if PR is already merged."""
        with patch("devteam.git.recovery.run_gh") as mock_gh:
            mock_gh.return_value = {"state": "MERGED"}
            assert check_pr_merged(tmp_path, 42) is True

    def test_pr_not_merged(self, tmp_path: Path):
        """Returns False if PR is still open."""
        with patch("devteam.git.recovery.run_gh") as mock_gh:
            mock_gh.return_value = {"state": "OPEN"}
            assert check_pr_merged(tmp_path, 42) is False


class TestResetWorktreeToClean:
    def test_reset_discards_changes(self, git_repo: Path):
        """Reset brings worktree back to last commit."""
        (git_repo / "dirty.txt").write_text("uncommitted")
        subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True, capture_output=True)
        reset_worktree_to_clean(git_repo)
        check = check_worktree_state(git_repo)
        assert check.clean is True

    def test_reset_idempotent(self, git_repo: Path):
        """Reset on a clean worktree is a no-op."""
        reset_worktree_to_clean(git_repo)
        check = check_worktree_state(git_repo)
        assert check.clean is True
```

Run:
```bash
pixi run pytest tests/git/test_recovery.py -x
```
Expected: All tests fail (ImportError).

- [ ] **Step 2 (5 min):** Implement recovery checks.

`src/devteam/git/recovery.py`:
```python
"""Idempotent recovery checks for git/GitHub side effects.

Pattern: before any side-effecting step, check if the effect already
happened. Every external action is idempotent on retry.

Used by the DBOS workflow layer to safely resume after crashes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from devteam.git._subprocess import GitError, run_git, run_gh
from devteam.git.branch import branch_exists_remote
from devteam.git.pr import PRInfo, find_existing_pr


@dataclass
class RecoveryCheck:
    """Result of checking a worktree's state for recovery."""

    exists: bool = True
    clean: bool = True
    details: str = ""


def check_worktree_state(worktree_path: Path) -> RecoveryCheck:
    """Check whether a worktree exists and is clean.

    Used before retrying a failed agent step — if the worktree is dirty,
    it should be reset before re-running.

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        RecoveryCheck with exists and clean flags.
    """
    if not worktree_path.exists():
        return RecoveryCheck(exists=False, clean=False, details="path does not exist")

    try:
        status = run_git(["status", "--porcelain"], cwd=worktree_path)
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
    return branch_exists_remote(repo_root, branch, remote=remote)


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
        data = run_gh(
            ["pr", "view", str(pr_number), "--json", "state"],
            cwd=cwd,
            parse_json=True,
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
    run_git(["reset", "--hard", "HEAD"], cwd=worktree_path)
    run_git(["clean", "-fd"], cwd=worktree_path)
```

Run:
```bash
pixi run pytest tests/git/test_recovery.py -x -v
```
Expected: All 9 tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/devteam/git/recovery.py tests/git/test_recovery.py
git commit -m "feat: idempotent recovery checks for worktree, branch, PR state"
```

---

## Task 9: Same-repo concurrency detection

**Files:**
- Modify: `src/devteam/git/recovery.py` (add concurrency check)
- Modify: `tests/git/test_recovery.py` (add concurrency tests)

- [ ] **Step 1 (2 min):** Write the failing tests.

Append to `tests/git/test_recovery.py`:
```python
from devteam.git.recovery import check_same_repo_concurrency


class TestSameRepoConcurrency:
    def test_detects_conflict(self):
        """Detects when two jobs target the same repo."""
        active_jobs = [
            {"job_id": "W-1", "repo": "org/myapp"},
            {"job_id": "W-2", "repo": "org/other"},
        ]
        result = check_same_repo_concurrency("org/myapp", active_jobs)
        assert result is not None
        assert result["job_id"] == "W-1"

    def test_no_conflict(self):
        """No conflict when targeting a different repo."""
        active_jobs = [
            {"job_id": "W-1", "repo": "org/other"},
        ]
        result = check_same_repo_concurrency("org/myapp", active_jobs)
        assert result is None

    def test_empty_active_jobs(self):
        """No conflict when no active jobs."""
        result = check_same_repo_concurrency("org/myapp", [])
        assert result is None
```

Run:
```bash
pixi run pytest tests/git/test_recovery.py::TestSameRepoConcurrency -x
```
Expected: All tests fail (ImportError).

- [ ] **Step 2 (2 min):** Implement concurrency detection.

Append to `src/devteam/git/recovery.py`:
```python
def check_same_repo_concurrency(
    target_repo: str,
    active_jobs: list[dict[str, str]],
) -> dict[str, str] | None:
    """Check if another active job targets the same repository.

    Used at `devteam start` time to warn the operator about concurrent
    work on the same repo.

    Args:
        target_repo: Repository the new job will target ('owner/name').
        active_jobs: List of dicts with 'job_id' and 'repo' keys.

    Returns:
        The conflicting job dict if found, None otherwise.
    """
    for job in active_jobs:
        if job.get("repo") == target_repo:
            return job
    return None
```

Run:
```bash
pixi run pytest tests/git/test_recovery.py -x -v
```
Expected: All 12 tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/devteam/git/recovery.py tests/git/test_recovery.py
git commit -m "feat: same-repo concurrency detection for job start"
```

---

## Task 10: CLI commands — cancel, merge, takeover, handback

**Files:**
- Create: `src/devteam/cli/commands/git_commands.py`
- Create: `tests/cli/test_git_commands.py`

- [ ] **Step 1 (3 min):** Write the failing tests.

`tests/cli/test_git_commands.py`:
```python
"""Tests for git-related CLI commands."""

from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from typer.testing import CliRunner

from devteam.cli.commands.git_commands import git_app


runner = CliRunner()


class TestCancelCommand:
    def test_cancel_job(self):
        """devteam cancel W-1 triggers full cleanup."""
        with patch("devteam.cli.commands.git_commands.send_cancel_request") as mock:
            mock.return_value = {
                "success": True,
                "cleaned": [
                    {"action": "pr_closed", "branch": "feat/a", "pr_number": 12},
                ],
                "preserved": [],
            }
            result = runner.invoke(git_app, ["cancel", "W-1"])
            assert result.exit_code == 0
            assert "CANCELLED" in result.output or "Cancelled" in result.output

    def test_cancel_nonexistent_job(self):
        """Cancel on a nonexistent job shows an error."""
        with patch("devteam.cli.commands.git_commands.send_cancel_request") as mock:
            mock.return_value = {"success": False, "error": "Job W-99 not found"}
            result = runner.invoke(git_app, ["cancel", "W-99"])
            assert result.exit_code == 1 or "not found" in result.output.lower()


class TestMergeCommand:
    def test_merge_pr(self):
        """devteam merge triggers merge with check verification."""
        with patch("devteam.cli.commands.git_commands.send_merge_request") as mock:
            mock.return_value = {"success": True, "pr_number": 42, "merged": True}
            result = runner.invoke(git_app, ["merge", "W-1/PR-42"])
            assert result.exit_code == 0

    def test_merge_failing_pr(self):
        """Refuses to merge a PR with failing checks."""
        with patch("devteam.cli.commands.git_commands.send_merge_request") as mock:
            mock.return_value = {
                "success": False,
                "error": "CI checks not passed",
                "failed_checks": ["lint", "test"],
            }
            result = runner.invoke(git_app, ["merge", "W-1/PR-42"])
            assert result.exit_code == 1 or "not passed" in result.output.lower()


class TestTakeoverCommand:
    def test_takeover_shows_worktree(self):
        """devteam takeover outputs worktree path."""
        with patch("devteam.cli.commands.git_commands.send_takeover_request") as mock:
            mock.return_value = {
                "success": True,
                "worktree_path": "/repo/.worktrees/feat-auth",
                "task_id": "T-3",
            }
            result = runner.invoke(git_app, ["takeover", "W-1/T-3"])
            assert result.exit_code == 0
            assert ".worktrees/feat-auth" in result.output


class TestHandbackCommand:
    def test_handback_validates(self):
        """devteam handback runs validation before resuming."""
        with patch("devteam.cli.commands.git_commands.send_handback_request") as mock:
            mock.return_value = {
                "success": True,
                "validation": {"clean": True, "scope_ok": True},
            }
            result = runner.invoke(git_app, ["handback", "W-1/T-3"])
            assert result.exit_code == 0

    def test_handback_dirty_worktree(self):
        """Handback rejects dirty worktree."""
        with patch("devteam.cli.commands.git_commands.send_handback_request") as mock:
            mock.return_value = {
                "success": False,
                "error": "Worktree has uncommitted changes",
                "validation": {"clean": False},
            }
            result = runner.invoke(git_app, ["handback", "W-1/T-3"])
            assert result.exit_code == 1 or "uncommitted" in result.output.lower()
```

Run:
```bash
pixi run pytest tests/cli/test_git_commands.py -x
```
Expected: All tests fail (ImportError).

- [ ] **Step 2 (5 min):** Implement the CLI commands.

```bash
mkdir -p tests/cli
touch tests/cli/__init__.py
```

`src/devteam/cli/commands/git_commands.py`:
```python
"""Git lifecycle CLI commands — cancel, merge, takeover, handback.

These commands send requests to the daemon, which performs the actual
git/GitHub operations. The CLI only formats output.
"""

from __future__ import annotations

import sys
from typing import Any

import typer

git_app = typer.Typer(name="git", help="Git lifecycle commands")


def send_cancel_request(job_id: str) -> dict[str, Any]:
    """Send a cancel request to the daemon. Placeholder for httpx call."""
    raise NotImplementedError("Daemon integration in Plan 1")


def send_merge_request(pr_ref: str) -> dict[str, Any]:
    """Send a merge request to the daemon. Placeholder for httpx call."""
    raise NotImplementedError("Daemon integration in Plan 1")


def send_takeover_request(task_ref: str) -> dict[str, Any]:
    """Send a takeover request to the daemon. Placeholder for httpx call."""
    raise NotImplementedError("Daemon integration in Plan 1")


def send_handback_request(task_ref: str) -> dict[str, Any]:
    """Send a handback request to the daemon. Placeholder for httpx call."""
    raise NotImplementedError("Daemon integration in Plan 1")


@git_app.command("cancel")
def cancel_command(
    job_id: str = typer.Argument(help="Job ID to cancel (e.g. W-1)"),
    revert_merged: bool = typer.Option(
        False, "--revert-merged", help="Create revert PRs for merged work"
    ),
) -> None:
    """Cancel a job and clean up all worktrees, branches, and PRs."""
    result = send_cancel_request(job_id)

    if not result.get("success"):
        typer.echo(f"Error: {result.get('error', 'Unknown error')}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"[{job_id}] CANCELLED\n")

    cleaned = result.get("cleaned", [])
    if cleaned:
        typer.echo("  Cleaned up:")
        for item in cleaned:
            action = item.get("action", "")
            branch = item.get("branch", "")
            pr = item.get("pr_number", "")
            if pr:
                typer.echo(f"    Closed PR #{pr} {branch}")
            else:
                typer.echo(f"    {action} {branch}")

    preserved = result.get("preserved", [])
    if preserved:
        typer.echo("\n  Preserved (already merged):")
        for item in preserved:
            pr = item.get("pr_number", "")
            branch = item.get("branch", "")
            typer.echo(f"    PR #{pr} {branch} — merged before cancel")


@git_app.command("merge")
def merge_command(
    pr_ref: str = typer.Argument(
        help="PR reference (e.g. W-1/PR-42)"
    ),
) -> None:
    """Manually merge a PR (only when merge=manual in config).

    Verifies all checks passed before merging. Will not force-merge
    a failing PR.
    """
    result = send_merge_request(pr_ref)

    if not result.get("success"):
        error = result.get("error", "Unknown error")
        typer.echo(f"Error: {error}", err=True)
        failed = result.get("failed_checks", [])
        if failed:
            typer.echo("  Failed checks:", err=True)
            for check in failed:
                typer.echo(f"    - {check}", err=True)
        raise typer.Exit(code=1)

    pr_number = result.get("pr_number", "?")
    typer.echo(f"PR #{pr_number} merged successfully.")


@git_app.command("takeover")
def takeover_command(
    task_ref: str = typer.Argument(
        help="Task reference (e.g. W-1/T-3)"
    ),
) -> None:
    """Pause a task and get the worktree path for manual editing.

    After editing, commit your changes and run `devteam handback`.
    """
    result = send_takeover_request(task_ref)

    if not result.get("success"):
        typer.echo(f"Error: {result.get('error', 'Unknown error')}", err=True)
        raise typer.Exit(code=1)

    worktree = result.get("worktree_path", "")
    task_id = result.get("task_id", task_ref)
    typer.echo(f"Task {task_id} paused for manual editing.\n")
    typer.echo(f"Worktree: {worktree}")
    typer.echo(f"\nWhen done, commit your changes and run:")
    typer.echo(f"  devteam handback {task_ref}")


@git_app.command("handback")
def handback_command(
    task_ref: str = typer.Argument(
        help="Task reference (e.g. W-1/T-3)"
    ),
) -> None:
    """Resume a task after manual editing.

    Runs validation checks before resuming:
    - Worktree must have a clean working tree
    - No force pushes detected
    - Changed files within expected scope
    """
    result = send_handback_request(task_ref)

    if not result.get("success"):
        error = result.get("error", "Unknown error")
        typer.echo(f"Handback failed: {error}", err=True)
        validation = result.get("validation", {})
        if not validation.get("clean", True):
            typer.echo("  Worktree has uncommitted changes. Commit first.", err=True)
        if not validation.get("scope_ok", True):
            typer.echo(
                "  Warning: files outside expected scope were modified.",
                err=True,
            )
        raise typer.Exit(code=1)

    typer.echo(f"Task {task_ref} resumed. Entering review stage.")
```

Run:
```bash
pixi run pytest tests/cli/test_git_commands.py -x -v
```
Expected: All 6 tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/devteam/cli/commands/git_commands.py tests/cli/
git commit -m "feat: CLI commands for cancel, merge, takeover, handback"
```

---

## Task 11: Package exports and integration test

**Files:**
- Modify: `src/devteam/git/__init__.py`
- Create: `tests/git/test_integration.py`

- [ ] **Step 1 (2 min):** Set up the package exports.

`src/devteam/git/__init__.py`:
```python
"""Git lifecycle management — worktrees, branches, PRs, forks, cleanup.

All operations follow the idempotent recovery pattern: before any
side-effecting step, check if the effect already happened.
"""

from devteam.git.worktree import (
    WorktreeInfo,
    create_worktree,
    remove_worktree,
    list_worktrees,
    worktree_exists,
)
from devteam.git.branch import (
    create_feature_branch,
    delete_local_branch,
    delete_remote_branch,
    branch_exists_local,
    branch_exists_remote,
    get_current_branch,
    get_default_branch,
)
from devteam.git.pr import (
    PRInfo,
    PRCheckStatus,
    PRFeedback,
    create_pr,
    check_pr_status,
    merge_pr,
    close_pr,
    find_existing_pr,
)
from devteam.git.fork import (
    ForkStatus,
    check_push_access,
    find_existing_fork,
    ensure_fork,
    setup_fork_remotes,
)
from devteam.git.cleanup import (
    CleanupResult,
    CleanupAction,
    cleanup_after_merge,
    cleanup_on_cancel,
    cleanup_single_pr,
)
from devteam.git.recovery import (
    RecoveryCheck,
    check_worktree_state,
    check_branch_pushed,
    check_pr_exists,
    check_pr_merged,
    reset_worktree_to_clean,
    check_same_repo_concurrency,
)

__all__ = [
    # Worktree
    "WorktreeInfo",
    "create_worktree",
    "remove_worktree",
    "list_worktrees",
    "worktree_exists",
    # Branch
    "create_feature_branch",
    "delete_local_branch",
    "delete_remote_branch",
    "branch_exists_local",
    "branch_exists_remote",
    "get_current_branch",
    "get_default_branch",
    # PR
    "PRInfo",
    "PRCheckStatus",
    "PRFeedback",
    "create_pr",
    "check_pr_status",
    "merge_pr",
    "close_pr",
    "find_existing_pr",
    # Fork
    "ForkStatus",
    "check_push_access",
    "find_existing_fork",
    "ensure_fork",
    "setup_fork_remotes",
    # Cleanup
    "CleanupResult",
    "CleanupAction",
    "cleanup_after_merge",
    "cleanup_on_cancel",
    "cleanup_single_pr",
    # Recovery
    "RecoveryCheck",
    "check_worktree_state",
    "check_branch_pushed",
    "check_pr_exists",
    "check_pr_merged",
    "reset_worktree_to_clean",
    "check_same_repo_concurrency",
]
```

- [ ] **Step 2 (4 min):** Write the integration test — full cancel cleanup flow.

`tests/git/test_integration.py`:
```python
"""Integration test: full cancel cleanup flow using real git repos.

Tests the complete lifecycle: create worktrees + branches, then cancel
with full cleanup, verify everything is removed.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from devteam.git import (
    create_worktree,
    create_feature_branch,
    branch_exists_local,
    worktree_exists,
    cleanup_on_cancel,
    check_worktree_state,
    CleanupAction,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    readme = repo / "README.md"
    readme.write_text("# Test")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


class TestFullCancelCleanupFlow:
    """End-to-end test: create work artifacts, then cancel and verify cleanup."""

    def test_complete_cancel_flow(self, git_repo: Path):
        """Create 3 worktrees/branches, cancel job, verify all cleaned up."""
        # Setup: create worktrees and branches like a real job would
        wt1 = create_worktree(git_repo, "feat/user-auth")
        wt2 = create_worktree(git_repo, "feat/auth-ui")
        wt3 = create_worktree(git_repo, "feat/project-init")

        # Verify they exist
        assert worktree_exists(git_repo, "feat/user-auth")
        assert worktree_exists(git_repo, "feat/auth-ui")
        assert worktree_exists(git_repo, "feat/project-init")
        assert branch_exists_local(git_repo, "feat/user-auth")
        assert branch_exists_local(git_repo, "feat/auth-ui")
        assert branch_exists_local(git_repo, "feat/project-init")

        # Cancel: one PR already merged, two open
        pr_branches = [
            {
                "branch": "feat/project-init",
                "pr_number": 11,
                "worktree_path": wt3.path,
                "merged": True,
            },
            {
                "branch": "feat/user-auth",
                "pr_number": 12,
                "worktree_path": wt1.path,
                "merged": False,
            },
            {
                "branch": "feat/auth-ui",
                "pr_number": 14,
                "worktree_path": wt2.path,
                "merged": False,
            },
        ]

        with patch("devteam.git.cleanup.close_pr") as mock_close:
            with patch("devteam.git.cleanup.delete_remote_branch"):
                result = cleanup_on_cancel(
                    repo_root=git_repo,
                    pr_branches=pr_branches,
                )

        # Verify cleanup
        assert result.success is True

        # Open PRs were closed
        assert mock_close.call_count == 2

        # Merged PR preserved
        assert len(result.preserved) == 1
        assert result.preserved[0]["pr_number"] == 11

        # Open worktrees removed
        assert not wt1.path.exists()
        assert not wt2.path.exists()

        # Merged worktree also removed (cleanup_on_cancel skips merged)
        # The merged worktree stays because it's in the preserved list
        assert wt3.path.exists()  # preserved

        # Open local branches deleted
        assert not branch_exists_local(git_repo, "feat/user-auth")
        assert not branch_exists_local(git_repo, "feat/auth-ui")

        # Merged local branch preserved
        assert branch_exists_local(git_repo, "feat/project-init")

    def test_cancel_idempotent_double_run(self, git_repo: Path):
        """Running cancel twice produces the same result."""
        wt = create_worktree(git_repo, "feat/double-cancel")

        pr_branches = [
            {
                "branch": "feat/double-cancel",
                "pr_number": 20,
                "worktree_path": wt.path,
                "merged": False,
            },
        ]

        with patch("devteam.git.cleanup.close_pr"):
            with patch("devteam.git.cleanup.delete_remote_branch"):
                result1 = cleanup_on_cancel(repo_root=git_repo, pr_branches=pr_branches)
                result2 = cleanup_on_cancel(repo_root=git_repo, pr_branches=pr_branches)

        assert result1.success is True
        assert result2.success is True

    def test_worktree_with_dirty_files_force_cleaned(self, git_repo: Path):
        """Worktrees with uncommitted changes are force-cleaned on cancel."""
        wt = create_worktree(git_repo, "feat/dirty-cancel")

        # Make the worktree dirty
        (wt.path / "dirty.txt").write_text("uncommitted work")

        state = check_worktree_state(wt.path)
        assert state.clean is False

        pr_branches = [
            {
                "branch": "feat/dirty-cancel",
                "pr_number": 30,
                "worktree_path": wt.path,
                "merged": False,
            },
        ]

        with patch("devteam.git.cleanup.close_pr"):
            with patch("devteam.git.cleanup.delete_remote_branch"):
                result = cleanup_on_cancel(repo_root=git_repo, pr_branches=pr_branches)

        assert result.success is True
        assert not wt.path.exists()
```

Run:
```bash
pixi run pytest tests/git/test_integration.py -x -v
```
Expected: All 3 tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/devteam/git/__init__.py tests/git/test_integration.py
git commit -m "feat: git package exports and full cancel cleanup integration test"
```

---

## Task 12: Run full test suite and verify

- [ ] **Step 1 (2 min):** Run all git tests together.

```bash
pixi run pytest tests/git/ -x -v --tb=short
```
Expected: All tests pass (approximately 50+ tests across all modules).

- [ ] **Step 2 (1 min):** Run all project tests to ensure nothing is broken.

```bash
pixi run pytest tests/ -x -v --tb=short
```
Expected: All tests pass.

- [ ] **Step 3: Final commit with all modules wired up.**

```bash
git add -A
git commit -m "chore: finalize Plan 4 — git lifecycle management complete"
```
