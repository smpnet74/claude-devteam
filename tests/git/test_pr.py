"""Tests for PR creation, status checking, and merge."""

from pathlib import Path
from unittest.mock import patch

import pytest

from devteam.git.pr import (
    PRInfo,
    PRCheckStatus,
    create_pr,
    check_pr_status,
    merge_pr,
    close_pr,
    find_existing_pr,
    categorize_coderabbit_comments,
)


class TestCreatePR:
    def test_create_pr_basic(self, tmp_path: Path):
        """Creates a PR via gh CLI -- gh pr create returns URL, not JSON."""
        with patch("devteam.git.pr.find_existing_pr") as mock_find:
            # First call (idempotency check) returns None, second (post-create fetch) returns info
            mock_find.side_effect = [
                None,
                PRInfo(number=42, url="https://github.com/org/repo/pull/42", branch="feat/login"),
            ]
            with patch("devteam.git.pr.gh_run") as mock_gh:
                mock_gh.return_value = "https://github.com/org/repo/pull/42"
                info = create_pr(
                    cwd=tmp_path,
                    title="Add login flow",
                    body="Implements user authentication",
                    branch="feat/login",
                    base="main",
                )
                assert info.number == 42
                assert info.url == "https://github.com/org/repo/pull/42"
                # Should NOT pass parse_json=True
                call_kwargs = mock_gh.call_args[1] if mock_gh.call_args[1] else {}
                assert call_kwargs.get("parse_json") is not True

    def test_create_pr_fallback_when_fetch_fails(self, tmp_path: Path):
        """When post-create fetch returns None, falls back to URL-parsed PRInfo."""
        with patch("devteam.git.pr.find_existing_pr", return_value=None):
            with patch("devteam.git.pr.gh_run") as mock_gh:
                mock_gh.return_value = "https://github.com/org/repo/pull/42"
                info = create_pr(
                    cwd=tmp_path,
                    title="Add login flow",
                    body="Implements user authentication",
                    branch="feat/login",
                    base="main",
                )
                assert info.number == 42
                assert info.url == "https://github.com/org/repo/pull/42"
                assert info.branch == "feat/login"
                assert info.state == "OPEN"

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
            with patch("devteam.git.pr.gh_run") as mock_gh:
                mock_gh.return_value = "https://github.com/org/repo/pull/99"
                _info = create_pr(
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
        with patch("devteam.git.pr.gh_run") as mock_gh:
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
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.return_value = []
            result = find_existing_pr(tmp_path, "feat/nope")
            assert result is None


class TestCheckPRStatus:
    def test_all_green(self, tmp_path: Path):
        """All CI checks pass, no review comments."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            # First call: checks (using bucket field), second call: reviews
            mock_gh.side_effect = [
                [
                    {"name": "ci", "state": "completed", "bucket": "pass"},
                    {"name": "lint", "state": "completed", "bucket": "pass"},
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
            assert not feedback.api_errors

    def test_ci_pending(self, tmp_path: Path):
        """CI checks still running."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.side_effect = [
                [
                    {"name": "ci", "state": "in_progress", "bucket": "pending"},
                ],
                {"reviews": [], "comments": [], "reviewDecision": ""},
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.ci_complete is False
            assert feedback.all_green is False

    def test_ci_failed(self, tmp_path: Path):
        """CI check failed."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.side_effect = [
                [
                    {"name": "ci", "state": "completed", "bucket": "fail"},
                ],
                {"reviews": [], "comments": [], "reviewDecision": ""},
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.ci_complete is True
            assert feedback.check_status == PRCheckStatus.SOME_FAILED
            assert "ci" in feedback.failed_checks

    def test_ci_cancelled(self, tmp_path: Path):
        """Cancelled CI check is treated as failed."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.side_effect = [
                [
                    {"name": "ci", "state": "completed", "bucket": "cancel"},
                ],
                {"reviews": [], "comments": [], "reviewDecision": ""},
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.check_status == PRCheckStatus.SOME_FAILED
            assert "ci" in feedback.failed_checks

    def test_ci_skipping(self, tmp_path: Path):
        """Skipped checks are non-blocking (e.g., path-filtered CI)."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.side_effect = [
                [
                    {"name": "ci", "state": "completed", "bucket": "pass"},
                    {"name": "deploy", "state": "completed", "bucket": "skipping"},
                ],
                {"reviews": [], "comments": [], "reviewDecision": "APPROVED"},
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.ci_complete is True
            assert feedback.check_status == PRCheckStatus.ALL_PASSED
            assert feedback.all_green is True
            assert not feedback.failed_checks

    def test_no_checks(self, tmp_path: Path):
        """Repo with no CI checks configured."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.side_effect = [
                [],  # no checks
                {"reviews": [], "comments": [], "reviewDecision": ""},
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.ci_complete is True
            assert feedback.check_status == PRCheckStatus.NO_CHECKS

    def test_api_error_tracked(self, tmp_path: Path):
        """API failures are tracked in api_errors and block all_green."""
        from devteam.git.helpers import GhError

        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.side_effect = [
                GhError(["pr", "checks"], 1, "network error"),
                {"reviews": [], "comments": [], "reviewDecision": "APPROVED"},
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert len(feedback.api_errors) == 1
            assert "Failed to fetch CI checks" in feedback.api_errors[0]
            assert feedback.all_green is False


class TestMergePR:
    def test_merge_squash(self, tmp_path: Path):
        """Merges a PR via squash merge."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            merge_pr(tmp_path, 42, strategy="squash")
            mock_gh.assert_called_once()
            call_str = str(mock_gh.call_args)
            assert "merge" in call_str
            assert "--squash" in call_str

    def test_merge_invalid_strategy(self, tmp_path: Path):
        """Rejects invalid merge strategy before calling gh."""
        with pytest.raises(ValueError, match="Invalid merge strategy"):
            merge_pr(tmp_path, 42, strategy="fast-forward")

    def test_merge_already_merged(self, tmp_path: Path):
        """Merging an already-merged PR is a no-op."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            from devteam.git.helpers import GhError

            mock_gh.side_effect = GhError(["pr", "merge"], 1, "already been merged")
            # Should not raise
            merge_pr(tmp_path, 42)


class TestClosePR:
    def test_close_pr(self, tmp_path: Path):
        """Closes a PR with a comment."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            close_pr(tmp_path, 42, comment="Cancelled by operator")
            assert mock_gh.call_count >= 1

    def test_close_already_closed(self, tmp_path: Path):
        """Closing an already-closed PR is a no-op."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            from devteam.git.helpers import GhError

            mock_gh.side_effect = GhError(["pr", "close"], 1, "already closed")
            close_pr(tmp_path, 42)

    def test_close_already_merged(self, tmp_path: Path):
        """Closing an already-merged PR is a no-op."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            from devteam.git.helpers import GhError

            mock_gh.side_effect = GhError(["pr", "close"], 1, "Already merged")
            close_pr(tmp_path, 42)


class TestAllGreenBlocking:
    def test_coderabbit_errors_block_all_green(self, tmp_path: Path):
        """all_green is False when CodeRabbit reports errors, even if CI passes."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.side_effect = [
                [{"name": "ci", "state": "completed", "bucket": "pass"}],
                {
                    "reviews": [],
                    "comments": [{"body": "[error] SQL injection", "author": "coderabbitai[bot]"}],
                    "reviewDecision": "APPROVED",
                },
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.check_status == PRCheckStatus.ALL_PASSED
            assert feedback.all_green is False

    def test_changes_requested_blocks_all_green(self, tmp_path: Path):
        """all_green is False when review decision is CHANGES_REQUESTED."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.side_effect = [
                [{"name": "ci", "state": "completed", "bucket": "pass"}],
                {
                    "reviews": [],
                    "comments": [],
                    "reviewDecision": "CHANGES_REQUESTED",
                },
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.all_green is False


class TestCodeRabbitCategorization:
    def test_categorize_comments(self):
        """CodeRabbit comments are sorted by severity."""
        comments = [
            {"body": "[nitpick] rename variable", "author": "coderabbitai[bot]"},
            {
                "body": "[error] SQL injection vulnerability",
                "author": "coderabbitai[bot]",
            },
            {"body": "[warning] missing null check", "author": "coderabbitai[bot]"},
            {"body": "looks good", "author": "human-reviewer"},
        ]
        categorized = categorize_coderabbit_comments(comments)
        assert len(categorized.errors) == 1
        assert len(categorized.warnings) == 1
        assert len(categorized.nitpicks) == 1
        assert "SQL injection" in categorized.errors[0]

    def test_categorize_with_dict_author(self):
        """Handles author as dict (GitHub API format)."""
        comments = [
            {"body": "[error] issue", "author": {"login": "coderabbitai[bot]"}},
        ]
        categorized = categorize_coderabbit_comments(comments)
        assert len(categorized.errors) == 1

    def test_empty_comments(self):
        """No CodeRabbit comments returns empty categories."""
        categorized = categorize_coderabbit_comments([])
        assert len(categorized.errors) == 0
        assert len(categorized.warnings) == 0
        assert len(categorized.nitpicks) == 0


class TestCheckPRStatusNonZeroExit:
    """gh pr checks returns non-zero for pending (exit 8) and failed checks."""

    def test_pending_checks_reported_correctly(self, tmp_path: Path):
        """Non-zero exit with valid JSON should still parse pending status."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.side_effect = [
                # gh pr checks with check=False returns JSON even on non-zero exit
                [
                    {"name": "ci", "state": "pending", "bucket": "pending"},
                    {"name": "lint", "state": "completed", "bucket": "pass"},
                ],
                {"reviews": [], "comments": [], "reviewDecision": ""},
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.check_status == PRCheckStatus.PENDING
            assert not feedback.ci_complete
            assert not feedback.api_errors

    def test_failed_checks_reported_correctly(self, tmp_path: Path):
        """Non-zero exit with failed checks should report SOME_FAILED."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.side_effect = [
                [
                    {"name": "ci", "state": "completed", "bucket": "fail"},
                    {"name": "lint", "state": "completed", "bucket": "pass"},
                ],
                {"reviews": [], "comments": [], "reviewDecision": ""},
            ]
            feedback = check_pr_status(tmp_path, 42)
            assert feedback.check_status == PRCheckStatus.SOME_FAILED
            assert "ci" in feedback.failed_checks
            assert feedback.all_green is False


class TestFindExistingPRErrors:
    """find_existing_pr should propagate real errors, not treat them as 'no PR'."""

    def test_auth_failure_propagates(self, tmp_path: Path):
        """Auth errors should raise, not return None."""
        from devteam.git.helpers import GhError

        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.side_effect = GhError(["pr", "list"], 1, "authentication required")
            with pytest.raises(GhError):
                find_existing_pr(tmp_path, "feat/test")

    def test_404_returns_none(self, tmp_path: Path):
        """404/not-found should return None (repo doesn't exist)."""
        from devteam.git.helpers import GhError

        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.side_effect = GhError(["pr", "list"], 1, "HTTP 404: Not Found")
            result = find_existing_pr(tmp_path, "feat/test")
            assert result is None


class TestCrossForkPRLookup:
    """find_existing_pr should disambiguate PRs from different forks."""

    def test_filters_by_expected_owner(self, tmp_path: Path):
        """When expected_owner is set, only PRs from that owner match."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.return_value = [
                {
                    "number": 10,
                    "url": "https://github.com/org/repo/pull/10",
                    "headRefName": "fix/test",
                    "state": "OPEN",
                    "headRepositoryOwner": {"login": "other-user"},
                },
                {
                    "number": 15,
                    "url": "https://github.com/org/repo/pull/15",
                    "headRefName": "fix/test",
                    "state": "OPEN",
                    "headRepositoryOwner": {"login": "my-fork"},
                },
            ]
            result = find_existing_pr(tmp_path, "fix/test", expected_owner="my-fork")
            assert result is not None
            assert result.number == 15

    def test_no_match_for_expected_owner(self, tmp_path: Path):
        """Returns None when no PR matches the expected owner."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.return_value = [
                {
                    "number": 10,
                    "url": "https://github.com/org/repo/pull/10",
                    "headRefName": "fix/test",
                    "state": "OPEN",
                    "headRepositoryOwner": {"login": "other-user"},
                },
            ]
            result = find_existing_pr(tmp_path, "fix/test", expected_owner="my-fork")
            assert result is None

    def test_no_owner_filter_returns_first(self, tmp_path: Path):
        """Without expected_owner, returns the first match (backward compatible)."""
        with patch("devteam.git.pr.gh_run") as mock_gh:
            mock_gh.return_value = [
                {
                    "number": 10,
                    "url": "https://github.com/org/repo/pull/10",
                    "headRefName": "fix/test",
                    "state": "OPEN",
                    "headRepositoryOwner": {"login": "anyone"},
                },
            ]
            result = find_existing_pr(tmp_path, "fix/test")
            assert result is not None
            assert result.number == 10
