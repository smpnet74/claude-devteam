"""Tests for git/gh subprocess helpers."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from devteam.git.helpers import (
    GitError,
    GhError,
    gh_run,
    get_current_branch,
    get_default_branch,
    get_repo_root,
    git_run,
)


class TestGitRun:
    def test_success(self, git_repo: Path) -> None:
        """git_run returns stdout on success."""
        result = git_run(["status"], cwd=git_repo)
        assert "On branch" in result

    def test_failure_raises_git_error(self, tmp_path: Path) -> None:
        """git_run raises GitError on non-zero exit."""
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(GitError, match="not a git repository|fatal"):
            git_run(["log"], cwd=empty)

    def test_strips_output(self, git_repo: Path) -> None:
        """git_run strips trailing whitespace/newlines."""
        result = git_run(["rev-parse", "--git-dir"], cwd=git_repo)
        assert result == ".git"

    def test_check_false_no_raise(self, tmp_path: Path) -> None:
        """git_run with check=False does not raise on failure."""
        empty = tmp_path / "empty"
        empty.mkdir()
        result = git_run(["log"], cwd=empty, check=False)
        # Returns empty stdout (or error message) without raising
        assert isinstance(result, str)

    def test_empty_args_raises_value_error(self) -> None:
        """git_run raises ValueError if args is empty."""
        with pytest.raises(ValueError, match="args must not be empty"):
            git_run([])

    def test_error_stores_command_returncode_stderr(self, tmp_path: Path) -> None:
        """GitError stores command, returncode, stderr for debugging."""
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(GitError) as exc_info:
            git_run(["log"], cwd=empty)
        err = exc_info.value
        assert err.command == ["log"]
        assert err.returncode != 0
        assert isinstance(err.stderr, str)


class TestGhRun:
    def test_success_mocked(self) -> None:
        """gh_run returns stdout on success (mocked)."""
        with patch("devteam.git.helpers.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh", "pr", "list"],
                returncode=0,
                stdout='[{"number": 1}]',
                stderr="",
            )
            result = gh_run(["pr", "list", "--json", "number"])
            assert '"number": 1' in result

    def test_failure_raises_gh_error(self) -> None:
        """gh_run raises GhError on non-zero exit."""
        with patch("devteam.git.helpers.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh", "pr", "view"],
                returncode=1,
                stdout="",
                stderr="no pull requests found",
            )
            with pytest.raises(GhError, match="no pull requests found"):
                gh_run(["pr", "view", "999"])

    def test_parse_json(self) -> None:
        """gh_run with parse_json=True returns parsed dict."""
        with patch("devteam.git.helpers.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh"],
                returncode=0,
                stdout='{"merged": true}',
                stderr="",
            )
            result = gh_run(["pr", "view", "1", "--json", "merged"], parse_json=True)
            assert result == {"merged": True}

    def test_parse_json_list(self) -> None:
        """gh_run with parse_json=True handles list responses."""
        with patch("devteam.git.helpers.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh"],
                returncode=0,
                stdout='[{"number": 1}, {"number": 2}]',
                stderr="",
            )
            result = gh_run(["pr", "list", "--json", "number"], parse_json=True)
            assert isinstance(result, list)
            assert len(result) == 2

    def test_empty_args_raises_value_error(self) -> None:
        """gh_run raises ValueError if args is empty."""
        with pytest.raises(ValueError, match="args must not be empty"):
            gh_run([])

    def test_error_stores_command_returncode_stderr(self) -> None:
        """GhError stores command, returncode, stderr for debugging."""
        with patch("devteam.git.helpers.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh", "api"],
                returncode=1,
                stdout="",
                stderr="HTTP 404: Not Found",
            )
            with pytest.raises(GhError) as exc_info:
                gh_run(["api", "repos/x/y"])
            err = exc_info.value
            assert err.command == ["api", "repos/x/y"]
            assert err.returncode == 1
            assert "Not Found" in err.stderr

    def test_parse_json_invalid_raises_gh_error(self) -> None:
        """gh_run with parse_json=True raises GhError on invalid JSON."""
        with patch("devteam.git.helpers.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh", "api", "repos/x/y"],
                returncode=0,
                stdout="not valid json at all",
                stderr="",
            )
            with pytest.raises(GhError, match="Failed to parse JSON"):
                gh_run(["api", "repos/x/y"], parse_json=True)


class TestGetRepoRoot:
    def test_returns_repo_root(self, git_repo: Path) -> None:
        """get_repo_root returns the repo root path."""
        root = get_repo_root(cwd=git_repo)
        assert root == git_repo.resolve()

    def test_from_subdirectory(self, git_repo: Path) -> None:
        """get_repo_root works from a subdirectory."""
        subdir = git_repo / "src" / "pkg"
        subdir.mkdir(parents=True)
        root = get_repo_root(cwd=subdir)
        assert root == git_repo.resolve()

    def test_not_a_repo_raises(self, tmp_path: Path) -> None:
        """get_repo_root raises GitError outside a repo."""
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(GitError):
            get_repo_root(cwd=empty)


class TestGetCurrentBranch:
    def test_returns_branch_name(self, git_repo: Path) -> None:
        """get_current_branch returns the current branch."""
        branch = get_current_branch(cwd=git_repo)
        assert branch in ("main", "master")

    def test_after_checkout(self, git_repo: Path) -> None:
        """get_current_branch reflects branch switches."""
        subprocess.run(
            ["git", "-C", str(git_repo), "checkout", "-b", "feat/test"],
            check=True,
            capture_output=True,
        )
        branch = get_current_branch(cwd=git_repo)
        assert branch == "feat/test"

    def test_detached_head_raises(self, git_repo: Path) -> None:
        """get_current_branch raises GitError on detached HEAD."""
        # Get the current commit SHA and checkout detached
        sha = subprocess.run(
            ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(git_repo), "checkout", sha],
            check=True,
            capture_output=True,
        )
        with pytest.raises(GitError, match="Detached HEAD"):
            get_current_branch(cwd=git_repo)


class TestGetDefaultBranch:
    def test_returns_main_or_master(self, git_repo: Path) -> None:
        """get_default_branch returns main or master."""
        branch = get_default_branch(cwd=git_repo)
        assert branch in ("main", "master")

    def test_returns_main_fallback(self, tmp_path: Path) -> None:
        """get_default_branch falls back to 'main' if neither exists."""
        # Create a repo with a different default branch
        repo = tmp_path / "oddrepo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=develop", str(repo)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "T"],
            check=True,
            capture_output=True,
        )
        (repo / "f.txt").write_text("x")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "init"],
            check=True,
            capture_output=True,
        )
        branch = get_default_branch(cwd=repo)
        assert branch == "main"  # fallback
