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
        """Creates a PR via gh CLI."""
        with patch("devteam.git.pr.find_existing_pr", return_value=None):
            with patch("devteam.git.pr.gh_run") as mock_gh:
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
            with patch("devteam.git.pr.gh_run") as mock_gh:
                mock_gh.return_value = {
                    "number": 99,
                    "url": "https://github.com/org/repo/pull/99",
                    "headRefName": "feat/fix",
                }
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
        with patch("devteam.git.pr.gh_run") as mock_gh:
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
        with patch("devteam.git.pr.gh_run") as mock_gh:
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
        with patch("devteam.git.pr.gh_run") as mock_gh:
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
            # Should not raise
            close_pr(tmp_path, 42)


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

    def test_empty_comments(self):
        """No CodeRabbit comments returns empty categories."""
        categorized = categorize_coderabbit_comments([])
        assert len(categorized.errors) == 0
        assert len(categorized.warnings) == 0
        assert len(categorized.nitpicks) == 0
