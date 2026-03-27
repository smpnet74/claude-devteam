"""Tests for PR feedback loop logic."""

from datetime import datetime, timezone

from devteam.git.pr_feedback import (
    FeedbackLoopConfig,
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
