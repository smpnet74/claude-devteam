"""State machine transitions for entity lifecycles.

Each entity type has a defined set of valid transitions. The validate_*_transition
functions enforce these rules and raise InvalidTransitionError on violations.
"""

from __future__ import annotations

from typing import Any

from devteam.models.entities import JobStatus, PRStatus, QuestionStatus, TaskStatus


class InvalidTransitionError(Exception):
    """Raised when an entity state transition is not allowed."""

    def __init__(self, entity_type: str, from_state: str, to_state: str) -> None:
        self.entity_type = entity_type
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Invalid {entity_type} transition: {from_state} -> {to_state}")


# --- Job Transitions ---
# created -> planning -> decomposing -> executing -> reviewing -> completed
#                                          <-> paused_rate_limit
#                                          -> failed
#                                          -> canceled (from any non-terminal)

JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.CREATED: {JobStatus.PLANNING, JobStatus.CANCELED},
    JobStatus.PLANNING: {JobStatus.DECOMPOSING, JobStatus.FAILED, JobStatus.CANCELED},
    JobStatus.DECOMPOSING: {JobStatus.EXECUTING, JobStatus.FAILED, JobStatus.CANCELED},
    JobStatus.EXECUTING: {
        JobStatus.REVIEWING,
        JobStatus.PAUSED_RATE_LIMIT,
        JobStatus.FAILED,
        JobStatus.CANCELED,
    },
    JobStatus.REVIEWING: {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED},
    JobStatus.PAUSED_RATE_LIMIT: {JobStatus.EXECUTING, JobStatus.CANCELED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: {JobStatus.CANCELED},
    JobStatus.CANCELED: set(),
}


# --- Task Transitions ---
# queued -> assigned -> executing -> waiting_on_review -> approved -> completed
#                          <-> waiting_on_question
#                          <-> waiting_on_ci
#                          -> paused
#                     waiting_on_review <-> revision_requested -> executing
#                     canceled (from any non-terminal)

TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.ASSIGNED, TaskStatus.CANCELED},
    TaskStatus.ASSIGNED: {TaskStatus.EXECUTING, TaskStatus.CANCELED},
    TaskStatus.EXECUTING: {
        TaskStatus.WAITING_ON_REVIEW,
        TaskStatus.WAITING_ON_QUESTION,
        TaskStatus.WAITING_ON_CI,
        TaskStatus.PAUSED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
    },
    TaskStatus.WAITING_ON_REVIEW: {
        TaskStatus.APPROVED,
        TaskStatus.REVISION_REQUESTED,
        TaskStatus.CANCELED,
    },
    TaskStatus.APPROVED: {TaskStatus.COMPLETED, TaskStatus.CANCELED},
    TaskStatus.WAITING_ON_QUESTION: {TaskStatus.EXECUTING, TaskStatus.CANCELED},
    TaskStatus.REVISION_REQUESTED: {TaskStatus.EXECUTING, TaskStatus.CANCELED},
    TaskStatus.WAITING_ON_CI: {TaskStatus.EXECUTING, TaskStatus.PAUSED, TaskStatus.CANCELED},
    TaskStatus.PAUSED: {TaskStatus.EXECUTING, TaskStatus.CANCELED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: {TaskStatus.CANCELED},
    TaskStatus.CANCELED: set(),
}


# --- Question Transitions ---
# raised -> escalated_to_supervisor -> escalated_to_leadership -> escalated_to_human -> resolved
# (resolved can also be reached from raised or any escalation level)

QUESTION_TRANSITIONS: dict[QuestionStatus, set[QuestionStatus]] = {
    QuestionStatus.RAISED: {
        QuestionStatus.ESCALATED_TO_SUPERVISOR,
        QuestionStatus.RESOLVED,
    },
    QuestionStatus.ESCALATED_TO_SUPERVISOR: {
        QuestionStatus.ESCALATED_TO_LEADERSHIP,
        QuestionStatus.RESOLVED,
    },
    QuestionStatus.ESCALATED_TO_LEADERSHIP: {
        QuestionStatus.ESCALATED_TO_HUMAN,
        QuestionStatus.RESOLVED,
    },
    QuestionStatus.ESCALATED_TO_HUMAN: {
        QuestionStatus.RESOLVED,
    },
    QuestionStatus.RESOLVED: set(),
}


# --- PR Transitions ---
# branch_created -> pr_opened -> waiting_on_ci -> ci_passed -> ready_for_merge -> merged -> cleaned_up
#                                 waiting_on_ci -> ci_failed -> fixing -> waiting_on_ci
#                                                               fixing -> escalated_to_human

PR_TRANSITIONS: dict[PRStatus, set[PRStatus]] = {
    PRStatus.BRANCH_CREATED: {PRStatus.PR_OPENED},
    PRStatus.PR_OPENED: {PRStatus.WAITING_ON_CI},
    PRStatus.WAITING_ON_CI: {PRStatus.CI_PASSED, PRStatus.CI_FAILED},
    PRStatus.CI_PASSED: {PRStatus.READY_FOR_MERGE},
    PRStatus.CI_FAILED: {PRStatus.FIXING},
    PRStatus.FIXING: {PRStatus.WAITING_ON_CI, PRStatus.ESCALATED_TO_HUMAN},
    PRStatus.READY_FOR_MERGE: {PRStatus.MERGED},
    PRStatus.MERGED: {PRStatus.CLEANED_UP},
    PRStatus.CLEANED_UP: set(),
    PRStatus.ESCALATED_TO_HUMAN: set(),
}


def _validate_transition(
    entity_type: str,
    transitions: Any,
    from_state: Any,
    to_state: Any,
) -> None:
    """Generic transition validator."""
    valid_targets = transitions.get(from_state, set())
    if to_state not in valid_targets:
        raise InvalidTransitionError(entity_type, from_state.value, to_state.value)


def validate_job_transition(from_state: JobStatus, to_state: JobStatus) -> None:
    """Validate a Job state transition."""
    _validate_transition("Job", JOB_TRANSITIONS, from_state, to_state)


def validate_task_transition(from_state: TaskStatus, to_state: TaskStatus) -> None:
    """Validate a Task state transition."""
    _validate_transition("Task", TASK_TRANSITIONS, from_state, to_state)


def validate_question_transition(from_state: QuestionStatus, to_state: QuestionStatus) -> None:
    """Validate a Question state transition."""
    _validate_transition("Question", QUESTION_TRANSITIONS, from_state, to_state)


def validate_pr_transition(from_state: PRStatus, to_state: PRStatus) -> None:
    """Validate a PR state transition."""
    _validate_transition("PR", PR_TRANSITIONS, from_state, to_state)
