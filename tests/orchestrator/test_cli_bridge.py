"""Tests for CLI bridge -- devteam start, comment, answer, status, cancel."""

import tempfile
from unittest.mock import patch

import pytest

from devteam.models.entities import JobStatus
from devteam.orchestrator.cli_bridge import (
    JobStore,
    QuestionTracker,
    handle_answer,
    handle_cancel,
    handle_comment,
    handle_start,
    handle_status,
    parse_intake,
)
from devteam.orchestrator.jobs import create_job
from devteam.orchestrator.routing import IntakeContext
from devteam.orchestrator.schemas import (
    QuestionRecord,
    QuestionType,
)


# ---------------------------------------------------------------------------
# parse_intake
# ---------------------------------------------------------------------------


class TestParseIntake:
    def test_spec_as_string(self) -> None:
        ctx = parse_intake(spec="Build an API")
        assert ctx.spec == "Build an API"

    def test_spec_as_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# My Spec\nBuild a thing")
            f.flush()
            ctx = parse_intake(spec=f.name)
            assert ctx.spec is not None
            assert "My Spec" in ctx.spec

    def test_issue_url(self) -> None:
        ctx = parse_intake(issue="https://github.com/org/repo/issues/42")
        assert ctx.issue_url == "https://github.com/org/repo/issues/42"

    def test_prompt(self) -> None:
        ctx = parse_intake(prompt="Fix the login bug")
        assert ctx.prompt == "Fix the login bug"

    def test_spec_and_plan(self) -> None:
        ctx = parse_intake(spec="spec", plan="plan")
        assert ctx.spec == "spec"
        assert ctx.plan == "plan"


# ---------------------------------------------------------------------------
# JobStore
# ---------------------------------------------------------------------------


class TestJobStore:
    def test_next_id_increments(self) -> None:
        store = JobStore()
        assert store.next_id() == "W-1"
        assert store.next_id() == "W-2"

    def test_save_and_get(self) -> None:
        store = JobStore()
        job = create_job("W-1", "Test", IntakeContext())
        store.save(job)
        assert store.get("W-1") is not None
        assert store.get("W-99") is None

    def test_list_jobs(self) -> None:
        store = JobStore()
        j1 = create_job("W-1", "First", IntakeContext())
        j2 = create_job("W-2", "Second", IntakeContext())
        store.save(j1)
        store.save(j2)
        jobs = store.list_jobs()
        assert len(jobs) == 2

    def test_pending_questions(self) -> None:
        store = JobStore()
        q1 = QuestionTracker(
            id="Q-1",
            task_id="T-1",
            job_id="W-1",
            record=QuestionRecord(
                question="x?",
                question_type=QuestionType.TECHNICAL,
            ),
        )
        q2 = QuestionTracker(
            id="Q-2",
            task_id="T-2",
            job_id="W-1",
            record=QuestionRecord(
                question="y?",
                question_type=QuestionType.TECHNICAL,
            ),
            resolved=True,
            answer="yes",
        )
        store.save_question(q1)
        store.save_question(q2)
        pending = store.get_pending_questions()
        assert len(pending) == 1
        assert pending[0].id == "Q-1"

    def test_pending_questions_filter_by_job(self) -> None:
        store = JobStore()
        q1 = QuestionTracker(
            id="Q-1",
            task_id="T-1",
            job_id="W-1",
            record=QuestionRecord(
                question="x?",
                question_type=QuestionType.TECHNICAL,
            ),
        )
        q2 = QuestionTracker(
            id="Q-2",
            task_id="T-1",
            job_id="W-2",
            record=QuestionRecord(
                question="y?",
                question_type=QuestionType.TECHNICAL,
            ),
        )
        store.save_question(q1)
        store.save_question(q2)
        assert len(store.get_pending_questions(job_id="W-1")) == 1
        assert len(store.get_pending_questions(job_id="W-2")) == 1
        assert len(store.get_pending_questions()) == 2

    def test_next_question_id(self) -> None:
        store = JobStore()
        assert store.next_question_id() == "Q-1"
        assert store.next_question_id() == "Q-2"


# ---------------------------------------------------------------------------
# handle_start
# ---------------------------------------------------------------------------


class TestHandleStart:
    def test_creates_job_with_intake(self) -> None:
        store = JobStore()
        job = handle_start(store, title="My App", spec="spec", plan="plan")
        assert job.id == "W-1"
        assert job.status == JobStatus.CREATED
        assert job.intake is not None
        assert job.intake.spec == "spec"
        assert job.intake.plan == "plan"

    def test_sequential_job_ids(self) -> None:
        store = JobStore()
        with patch("devteam.orchestrator.cli_bridge._detect_repo", return_value=None):
            j1 = handle_start(store, title="Job 1", prompt="do thing 1")
            j2 = handle_start(store, title="Job 2", prompt="do thing 2")
        assert j1.id == "W-1"
        assert j2.id == "W-2"

    def test_stored_in_store(self) -> None:
        store = JobStore()
        handle_start(store, title="Test", prompt="fix bug")
        assert store.get("W-1") is not None

    def test_detects_repo_from_cwd(self) -> None:
        """handle_start populates the repo field from git remote."""
        store = JobStore()
        with patch("devteam.orchestrator.cli_bridge.git_run", return_value="git@github.com:org/myapp.git"):
            job = handle_start(store, title="Test", prompt="fix", cwd=None)
        assert job.repo == "org/myapp"

    def test_same_repo_concurrency_blocked(self) -> None:
        """Starting two jobs on the same repo raises ValueError."""
        store = JobStore()
        with patch("devteam.orchestrator.cli_bridge.git_run", return_value="https://github.com/org/myapp"):
            handle_start(store, title="Job 1", prompt="a")
            with pytest.raises(ValueError, match="already has an active job"):
                handle_start(store, title="Job 2", prompt="b")

    def test_different_repo_not_blocked(self) -> None:
        """Starting jobs on different repos succeeds."""
        store = JobStore()
        with patch("devteam.orchestrator.cli_bridge.git_run", side_effect=[
            "https://github.com/org/app1",
            "https://github.com/org/app2",
        ]):
            j1 = handle_start(store, title="Job 1", prompt="a")
            j2 = handle_start(store, title="Job 2", prompt="b")
        assert j1.repo == "org/app1"
        assert j2.repo == "org/app2"


# ---------------------------------------------------------------------------
# handle_comment
# ---------------------------------------------------------------------------


class TestHandleComment:
    def test_comment_on_task(self) -> None:
        store = JobStore()
        job = handle_start(store, title="Test", prompt="test")

        success = handle_comment(store, "W-1/T-3", "Use PostgreSQL")
        assert success
        assert any("PostgreSQL" in msg for _, msg in job.comments)
        # Verify task_id is stored
        assert job.comments[0][0] == "T-3"

    def test_comment_shorthand_single_job(self) -> None:
        store = JobStore()
        job = handle_start(store, title="Test", prompt="test")

        success = handle_comment(store, "T-3", "feedback")
        assert success
        assert len(job.comments) == 1
        assert job.comments[0] == ("T-3", "feedback")

    def test_comment_nonexistent_job(self) -> None:
        store = JobStore()
        success = handle_comment(store, "W-99/T-1", "feedback")
        assert not success

    def test_comment_shorthand_no_jobs(self) -> None:
        store = JobStore()
        success = handle_comment(store, "T-1", "feedback")
        assert not success

    def test_comment_shorthand_multiple_jobs_raises(self) -> None:
        store = JobStore()
        with patch("devteam.orchestrator.cli_bridge._detect_repo", return_value=None):
            handle_start(store, title="Job 1", prompt="a")
            handle_start(store, title="Job 2", prompt="b")
        # Ambiguous -- raises ValueError with helpful message
        with pytest.raises(ValueError, match="Multiple jobs active"):
            handle_comment(store, "T-1", "feedback")


# ---------------------------------------------------------------------------
# handle_answer
# ---------------------------------------------------------------------------


class TestHandleAnswer:
    def test_answer_resolves_question(self) -> None:
        store = JobStore()
        q = QuestionTracker(
            id="Q-1",
            task_id="T-2",
            job_id="W-1",
            record=QuestionRecord(
                question="Redis or Memcached?",
                question_type=QuestionType.TECHNICAL,
            ),
        )
        store.save_question(q)

        result = handle_answer(store, "W-1/Q-1", "Use Redis")
        assert result is not None
        assert result.resolved
        assert result.answer == "Use Redis"

    def test_answer_wrong_job_id_raises(self) -> None:
        """Question ref with wrong job_id should raise ValueError."""
        store = JobStore()
        handle_start(store, title="Test", prompt="test")
        tracker = QuestionTracker(
            id=store.next_question_id(),
            task_id="T-1",
            job_id="W-1",
            record=QuestionRecord(
                question="What auth?",
                question_type=QuestionType.TECHNICAL,
                context="testing",
            ),
        )
        store.save_question(tracker)
        with pytest.raises(ValueError, match="belongs to job"):
            handle_answer(store, "W-99/Q-1", "Use JWT")

    def test_answer_shorthand(self) -> None:
        store = JobStore()
        q = QuestionTracker(
            id="Q-1",
            task_id="T-2",
            job_id="W-1",
            record=QuestionRecord(
                question="x?",
                question_type=QuestionType.TECHNICAL,
            ),
        )
        store.save_question(q)

        result = handle_answer(store, "Q-1", "answer")
        assert result is not None
        assert result.resolved

    def test_answer_records_pending_answer_on_job(self) -> None:
        """When an answer is recorded, it should be stored in job.pending_answers."""
        store = JobStore()
        job = handle_start(store, title="Test", prompt="test")
        q = QuestionTracker(
            id="Q-1",
            task_id="T-2",
            job_id=job.id,
            record=QuestionRecord(
                question="Redis or Memcached?",
                question_type=QuestionType.TECHNICAL,
            ),
        )
        store.save_question(q)

        result = handle_answer(store, "Q-1", "Use Redis")
        assert result is not None
        assert result.resolved
        # Verify pending_answers on the job
        updated_job = store.get(job.id)
        assert updated_job is not None
        assert updated_job.pending_answers.get("T-2") == "Use Redis"

    def test_answer_nonexistent_question(self) -> None:
        store = JobStore()
        result = handle_answer(store, "Q-99", "answer")
        assert result is None


# ---------------------------------------------------------------------------
# handle_status
# ---------------------------------------------------------------------------


class TestHandleStatus:
    def test_status_for_specific_job(self) -> None:
        store = JobStore()
        handle_start(store, title="My App", spec="spec")
        status = handle_status(store, "W-1")
        assert status["job_id"] == "W-1"
        assert status["status"] == "created"

    def test_status_for_nonexistent_job(self) -> None:
        store = JobStore()
        status = handle_status(store, "W-99")
        assert "error" in status

    def test_status_all_jobs(self) -> None:
        store = JobStore()
        with patch("devteam.orchestrator.cli_bridge._detect_repo", return_value=None):
            handle_start(store, title="Job 1", prompt="a")
            handle_start(store, title="Job 2", prompt="b")
        status = handle_status(store)
        assert "jobs" in status
        assert len(status["jobs"]) == 2  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# handle_cancel
# ---------------------------------------------------------------------------


class TestHandleCancel:
    def test_cancel_sets_flag(self) -> None:
        store = JobStore()
        job = handle_start(store, title="Test", prompt="test")
        assert not job.cancelled

        result = handle_cancel(store, "W-1")
        assert result
        assert job.cancelled

    def test_cancel_transitions_active_job(self) -> None:
        """Cancel should transition non-terminal jobs to CANCELED status."""
        store = JobStore()
        job = handle_start(store, title="Test", prompt="test")
        assert job.status == JobStatus.CREATED

        result = handle_cancel(store, "W-1")
        assert result
        assert job.cancelled
        assert job.status == JobStatus.CANCELED

    def test_cancel_nonexistent_job(self) -> None:
        store = JobStore()
        result = handle_cancel(store, "W-99")
        assert not result

    def test_cancel_terminal_job_returns_false(self) -> None:
        """Canceling an already-canceled job returns False."""
        store = JobStore()
        handle_start(store, title="Test", prompt="test")
        assert handle_cancel(store, "W-1")
        assert not handle_cancel(store, "W-1")
