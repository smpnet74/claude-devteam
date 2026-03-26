"""Tests for state machine transitions."""

import pytest

from devteam.models.entities import JobStatus, PRStatus, QuestionStatus, TaskStatus
from devteam.models.state import (
    InvalidTransitionError,
    validate_job_transition,
    validate_pr_transition,
    validate_question_transition,
    validate_task_transition,
)


class TestJobTransitions:
    def test_valid_forward_transition(self) -> None:
        validate_job_transition(JobStatus.CREATED, JobStatus.PLANNING)

    def test_valid_planning_to_decomposing(self) -> None:
        validate_job_transition(JobStatus.PLANNING, JobStatus.DECOMPOSING)

    def test_valid_executing_to_paused(self) -> None:
        validate_job_transition(JobStatus.EXECUTING, JobStatus.PAUSED_RATE_LIMIT)

    def test_valid_paused_to_executing(self) -> None:
        validate_job_transition(JobStatus.PAUSED_RATE_LIMIT, JobStatus.EXECUTING)

    def test_invalid_backward_transition(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_job_transition(JobStatus.COMPLETED, JobStatus.CREATED)

    def test_canceled_from_executing(self) -> None:
        validate_job_transition(JobStatus.EXECUTING, JobStatus.CANCELED)

    def test_cannot_leave_completed(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_job_transition(JobStatus.COMPLETED, JobStatus.EXECUTING)

    def test_cannot_leave_canceled(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_job_transition(JobStatus.CANCELED, JobStatus.EXECUTING)


class TestTaskTransitions:
    def test_valid_queued_to_assigned(self) -> None:
        validate_task_transition(TaskStatus.QUEUED, TaskStatus.ASSIGNED)

    def test_valid_assigned_to_executing(self) -> None:
        validate_task_transition(TaskStatus.ASSIGNED, TaskStatus.EXECUTING)

    def test_valid_executing_to_waiting_on_review(self) -> None:
        validate_task_transition(TaskStatus.EXECUTING, TaskStatus.WAITING_ON_REVIEW)

    def test_valid_revision_loop(self) -> None:
        validate_task_transition(TaskStatus.WAITING_ON_REVIEW, TaskStatus.REVISION_REQUESTED)
        validate_task_transition(TaskStatus.REVISION_REQUESTED, TaskStatus.EXECUTING)

    def test_valid_executing_to_waiting_on_question(self) -> None:
        validate_task_transition(TaskStatus.EXECUTING, TaskStatus.WAITING_ON_QUESTION)

    def test_valid_question_resolved_to_executing(self) -> None:
        validate_task_transition(TaskStatus.WAITING_ON_QUESTION, TaskStatus.EXECUTING)

    def test_canceled_from_any_nonterminal(self) -> None:
        nonterminal = [
            TaskStatus.QUEUED,
            TaskStatus.ASSIGNED,
            TaskStatus.EXECUTING,
            TaskStatus.WAITING_ON_REVIEW,
            TaskStatus.WAITING_ON_QUESTION,
            TaskStatus.WAITING_ON_CI,
            TaskStatus.PAUSED,
            TaskStatus.REVISION_REQUESTED,
            TaskStatus.FAILED,
        ]
        for status in nonterminal:
            validate_task_transition(status, TaskStatus.CANCELED)

    def test_cannot_cancel_completed(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_task_transition(TaskStatus.COMPLETED, TaskStatus.CANCELED)

    def test_invalid_skip_transition(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_task_transition(TaskStatus.QUEUED, TaskStatus.COMPLETED)


class TestQuestionTransitions:
    def test_valid_raised_to_escalated_supervisor(self) -> None:
        validate_question_transition(QuestionStatus.RAISED, QuestionStatus.ESCALATED_TO_SUPERVISOR)

    def test_valid_raised_to_resolved(self) -> None:
        validate_question_transition(QuestionStatus.RAISED, QuestionStatus.RESOLVED)

    def test_valid_supervisor_to_leadership(self) -> None:
        validate_question_transition(
            QuestionStatus.ESCALATED_TO_SUPERVISOR, QuestionStatus.ESCALATED_TO_LEADERSHIP
        )

    def test_valid_leadership_to_human(self) -> None:
        validate_question_transition(
            QuestionStatus.ESCALATED_TO_LEADERSHIP, QuestionStatus.ESCALATED_TO_HUMAN
        )

    def test_valid_human_to_resolved(self) -> None:
        validate_question_transition(QuestionStatus.ESCALATED_TO_HUMAN, QuestionStatus.RESOLVED)

    def test_cannot_leave_resolved(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_question_transition(QuestionStatus.RESOLVED, QuestionStatus.RAISED)


class TestPRTransitions:
    def test_valid_forward_flow(self) -> None:
        validate_pr_transition(PRStatus.BRANCH_CREATED, PRStatus.PR_OPENED)
        validate_pr_transition(PRStatus.PR_OPENED, PRStatus.WAITING_ON_CI)
        validate_pr_transition(PRStatus.WAITING_ON_CI, PRStatus.CI_PASSED)
        validate_pr_transition(PRStatus.CI_PASSED, PRStatus.READY_FOR_MERGE)
        validate_pr_transition(PRStatus.READY_FOR_MERGE, PRStatus.MERGED)
        validate_pr_transition(PRStatus.MERGED, PRStatus.CLEANED_UP)

    def test_ci_failure_loop(self) -> None:
        validate_pr_transition(PRStatus.WAITING_ON_CI, PRStatus.CI_FAILED)
        validate_pr_transition(PRStatus.CI_FAILED, PRStatus.FIXING)
        validate_pr_transition(PRStatus.FIXING, PRStatus.WAITING_ON_CI)

    def test_escalation_from_fixing(self) -> None:
        validate_pr_transition(PRStatus.FIXING, PRStatus.ESCALATED_TO_HUMAN)

    def test_cannot_skip_ci(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_pr_transition(PRStatus.PR_OPENED, PRStatus.MERGED)

    def test_canceled_from_early_stages(self) -> None:
        cancelable = [
            PRStatus.BRANCH_CREATED,
            PRStatus.PR_OPENED,
            PRStatus.WAITING_ON_CI,
            PRStatus.CI_FAILED,
            PRStatus.FIXING,
        ]
        for status in cancelable:
            validate_pr_transition(status, PRStatus.CANCELED)

    def test_cannot_cancel_merged(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_pr_transition(PRStatus.MERGED, PRStatus.CANCELED)

    def test_cannot_cancel_cleaned_up(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_pr_transition(PRStatus.CLEANED_UP, PRStatus.CANCELED)

    def test_canceled_from_ci_passed(self) -> None:
        validate_pr_transition(PRStatus.CI_PASSED, PRStatus.CANCELED)

    def test_canceled_from_ready_for_merge(self) -> None:
        validate_pr_transition(PRStatus.READY_FOR_MERGE, PRStatus.CANCELED)

    def test_canceled_is_terminal(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_pr_transition(PRStatus.CANCELED, PRStatus.BRANCH_CREATED)
