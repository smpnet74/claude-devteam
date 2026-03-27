"""Tests for fork detection and management.

ALL gh operations are MOCKED -- no real GitHub API calls.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from devteam.git.fork import (
    ForkInfo,
    ForkStatus,
    _parse_nwo_from_url,
    check_push_access,
    create_fork,
    ensure_fork,
    find_existing_fork,
    setup_fork_remotes,
)
from devteam.git.helpers import GhError


class TestCheckPushAccess:
    def test_has_push_access(self) -> None:
        """Returns True when user has push permissions."""
        with patch("devteam.git.fork.gh_run") as mock_gh:
            # gh --jq .permissions extracts the permissions object
            mock_gh.return_value = {"push": True}
            assert check_push_access("org/repo") is True
            mock_gh.assert_called_once_with(
                ["api", "repos/org/repo", "--jq", ".permissions"],
                parse_json=True,
            )

    def test_no_push_access(self) -> None:
        """Returns False when user lacks push permissions."""
        with patch("devteam.git.fork.gh_run") as mock_gh:
            # gh --jq .permissions extracts the permissions object
            mock_gh.return_value = {"push": False}
            assert check_push_access("org/repo") is False

    def test_api_error_returns_false(self) -> None:
        """Returns False on API errors (repo not found, etc)."""
        with patch("devteam.git.fork.gh_run") as mock_gh:
            mock_gh.side_effect = GhError(["api"], 1, "Not Found")
            assert check_push_access("org/private-repo") is False

    def test_empty_nwo_raises(self) -> None:
        """Empty repo NWO raises ValueError."""
        with pytest.raises(ValueError, match="owner/name"):
            check_push_access("")

    def test_malformed_nwo_raises(self) -> None:
        """Malformed repo NWO raises ValueError."""
        with pytest.raises(ValueError, match="owner/name"):
            check_push_access("noslash")


class TestFindExistingFork:
    def test_finds_fork(self) -> None:
        """Finds a fork when one exists."""
        with patch("devteam.git.fork.gh_run") as mock_gh:
            mock_gh.return_value = [
                {
                    "nameWithOwner": "myuser/repo",
                    "parent": {"nameWithOwner": "org/repo"},
                },
                {
                    "nameWithOwner": "myuser/other",
                    "parent": {"nameWithOwner": "org/other"},
                },
            ]
            result = find_existing_fork("org/repo")
            assert result == "myuser/repo"

    def test_no_fork_found(self) -> None:
        """Returns None when no fork exists."""
        with patch("devteam.git.fork.gh_run") as mock_gh:
            mock_gh.return_value = []
            result = find_existing_fork("org/repo")
            assert result is None

    def test_gh_error_returns_none(self) -> None:
        """Returns None on gh errors."""
        with patch("devteam.git.fork.gh_run") as mock_gh:
            mock_gh.side_effect = GhError(["repo", "list"], 1, "auth required")
            result = find_existing_fork("org/repo")
            assert result is None

    def test_empty_nwo_raises(self) -> None:
        """Empty upstream NWO raises ValueError."""
        with pytest.raises(ValueError, match="owner/name"):
            find_existing_fork("")

    def test_no_matching_parent(self) -> None:
        """Returns None when forks exist but none match."""
        with patch("devteam.git.fork.gh_run") as mock_gh:
            mock_gh.return_value = [
                {
                    "nameWithOwner": "myuser/unrelated",
                    "parent": {"nameWithOwner": "other/unrelated"},
                },
            ]
            result = find_existing_fork("org/repo")
            assert result is None


class TestCreateFork:
    def test_create_fork(self) -> None:
        """Creates a fork and returns the fork NWO."""
        with patch("devteam.git.fork.gh_run") as mock_gh:
            with patch("devteam.git.fork.find_existing_fork", return_value="myuser/repo"):
                result = create_fork("org", "repo")
                assert result == "myuser/repo"
                mock_gh.assert_called_once_with(
                    ["repo", "fork", "org/repo", "--clone=false"],
                )

    def test_empty_owner_raises(self) -> None:
        """Empty owner raises ValueError."""
        with pytest.raises(ValueError, match="owner must not be empty"):
            create_fork("", "repo")

    def test_empty_repo_raises(self) -> None:
        """Empty repo raises ValueError."""
        with pytest.raises(ValueError, match="repo must not be empty"):
            create_fork("org", "")


class TestEnsureFork:
    def test_has_push_access(self) -> None:
        """Returns DIRECT when push access exists."""
        with patch("devteam.git.fork.check_push_access", return_value=True):
            status = ensure_fork("org/repo")
            assert status == ForkStatus.DIRECT

    def test_existing_fork(self) -> None:
        """Returns EXISTING_FORK when a fork is found."""
        with patch("devteam.git.fork.check_push_access", return_value=False):
            with patch("devteam.git.fork.find_existing_fork", return_value="myuser/repo"):
                status = ensure_fork("org/repo")
                assert status == ForkStatus.EXISTING_FORK

    def test_creates_new_fork(self) -> None:
        """Creates a fork when none exists and returns NEW_FORK."""
        with patch("devteam.git.fork.check_push_access", return_value=False):
            with patch("devteam.git.fork.find_existing_fork", return_value=None):
                with patch("devteam.git.fork.gh_run") as mock_gh:
                    status = ensure_fork("org/repo")
                    assert status == ForkStatus.NEW_FORK
                    mock_gh.assert_called_once_with(
                        ["repo", "fork", "org/repo", "--clone=false"],
                    )

    def test_empty_nwo_raises(self) -> None:
        """Empty upstream NWO raises ValueError."""
        with pytest.raises(ValueError, match="owner/name"):
            ensure_fork("")


class TestSetupForkRemotes:
    def test_setup_remotes(self, tmp_path: Path) -> None:
        """Configures origin as fork, upstream as original."""
        with patch("devteam.git.fork.git_run") as mock_git:
            setup_fork_remotes(tmp_path, "org/repo", "myuser/repo")
            calls = mock_git.call_args_list
            # Should set origin to the fork and upstream to original
            assert any("set-url" in str(c) and "myuser/repo" in str(c) for c in calls)
            assert any("upstream" in str(c) and "org/repo" in str(c) for c in calls)

    def test_setup_remotes_adds_on_set_url_failure(self, tmp_path: Path) -> None:
        """Falls back to remote add when set-url fails."""
        from devteam.git.helpers import GitError

        call_count = 0

        def side_effect(args: list[str], cwd: Path | None = None) -> str:
            nonlocal call_count
            call_count += 1
            if "set-url" in args:
                raise GitError(args, 1, "No such remote")
            return ""

        with patch("devteam.git.fork.git_run", side_effect=side_effect):
            setup_fork_remotes(tmp_path, "org/repo", "myuser/repo")
        # Should have called set-url twice (failed) then add twice (succeeded)
        assert call_count == 4

    def test_empty_upstream_raises(self, tmp_path: Path) -> None:
        """Empty upstream NWO raises ValueError."""
        with pytest.raises(ValueError, match="upstream_nwo must not be empty"):
            setup_fork_remotes(tmp_path, "", "myuser/repo")

    def test_empty_fork_raises(self, tmp_path: Path) -> None:
        """Empty fork NWO raises ValueError."""
        with pytest.raises(ValueError, match="fork_nwo must not be empty"):
            setup_fork_remotes(tmp_path, "org/repo", "")


class TestForkInfo:
    def test_frozen(self) -> None:
        """ForkInfo is frozen (immutable)."""
        info = ForkInfo(
            owner="org",
            repo="repo",
            clone_url="https://github.com/org/repo.git",
            is_fork=False,
        )
        with pytest.raises(AttributeError):
            info.owner = "other"  # type: ignore[misc]

    def test_with_parent(self) -> None:
        """ForkInfo can represent a fork with parent info."""
        info = ForkInfo(
            owner="myuser",
            repo="repo",
            clone_url="https://github.com/myuser/repo.git",
            is_fork=True,
            parent_owner="org",
            parent_repo="repo",
        )
        assert info.is_fork is True
        assert info.parent_owner == "org"


class TestForkStatus:
    def test_enum_values(self) -> None:
        """ForkStatus has expected values."""
        assert ForkStatus.DIRECT.value == "direct"
        assert ForkStatus.EXISTING_FORK.value == "existing_fork"
        assert ForkStatus.NEW_FORK.value == "new_fork"


class TestParseNwoFromUrl:
    def test_https_with_git_suffix(self) -> None:
        result = _parse_nwo_from_url("https://github.com/org/repo.git")
        assert result == "org/repo"

    def test_https_without_git_suffix(self) -> None:
        result = _parse_nwo_from_url("https://github.com/org/repo")
        assert result == "org/repo"

    def test_ssh_with_git_suffix(self) -> None:
        result = _parse_nwo_from_url("git@github.com:org/repo.git")
        assert result == "org/repo"

    def test_ssh_without_git_suffix(self) -> None:
        result = _parse_nwo_from_url("git@github.com:org/repo")
        assert result == "org/repo"

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_nwo_from_url("not-a-url")
