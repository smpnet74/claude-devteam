"""Tests for entity models."""

from datetime import datetime

import pytest

from devteam.models.entities import (
    Job,
    JobStatus,
    PRGroup,
    PRStatus,
    Priority,
    Question,
    QuestionStatus,
    Task,
    TaskStatus,
)


class TestJobModel:
    def test_create_job(self) -> None:
        job = Job(
            job_id="W-1",
            title="My App",
            spec_path="/path/to/spec.md",
            plan_path="/path/to/plan.md",
        )
        assert job.job_id == "W-1"
        assert job.status == JobStatus.CREATED
        assert job.title == "My App"
        assert isinstance(job.created_at, datetime)

    def test_job_id_format(self) -> None:
        with pytest.raises(ValueError):
            Job(job_id="invalid", title="Bad ID")

    def test_job_default_priority(self) -> None:
        job = Job(job_id="W-1", title="Test")
        assert job.priority == Priority.NORMAL

    def test_job_tracks_apps(self) -> None:
        job = Job(job_id="W-1", title="Test", apps=["api-service", "frontend"])
        assert len(job.apps) == 2


class TestTaskModel:
    def test_create_task(self) -> None:
        task = Task(
            task_id="T-1",
            job_id="W-1",
            description="Build API schema",
            assigned_to="backend",
            app="api-service",
        )
        assert task.task_id == "T-1"
        assert task.status == TaskStatus.QUEUED
        assert task.assigned_to == "backend"

    def test_task_id_format(self) -> None:
        with pytest.raises(ValueError):
            Task(task_id="bad", job_id="W-1", description="x", assigned_to="backend", app="api")

    def test_task_dependencies(self) -> None:
        task = Task(
            task_id="T-3",
            job_id="W-1",
            description="CI pipeline",
            assigned_to="devops",
            app="api-service",
            depends_on=["T-1", "T-2"],
        )
        assert task.depends_on == ["T-1", "T-2"]

    def test_task_display_id(self) -> None:
        task = Task(
            task_id="T-1",
            job_id="W-1",
            description="Test",
            assigned_to="backend",
            app="api",
        )
        assert task.display_id == "W-1/T-1"

    def test_task_pr_group(self) -> None:
        task = Task(
            task_id="T-1",
            job_id="W-1",
            description="Test",
            assigned_to="backend",
            app="api",
            pr_group="feat/user-auth",
        )
        assert task.pr_group == "feat/user-auth"


class TestTaskJobIdValidation:
    def test_task_rejects_invalid_job_id(self) -> None:
        with pytest.raises(ValueError):
            Task(
                task_id="T-1",
                job_id="invalid",
                description="x",
                assigned_to="backend",
                app="api",
            )

    def test_task_rejects_empty_job_id(self) -> None:
        with pytest.raises(ValueError):
            Task(
                task_id="T-1",
                job_id="",
                description="x",
                assigned_to="backend",
                app="api",
            )

    def test_task_rejects_wrong_prefix_job_id(self) -> None:
        with pytest.raises(ValueError):
            Task(
                task_id="T-1",
                job_id="T-1",
                description="x",
                assigned_to="backend",
                app="api",
            )

    def test_task_accepts_valid_job_id(self) -> None:
        task = Task(
            task_id="T-1",
            job_id="W-42",
            description="x",
            assigned_to="backend",
            app="api",
        )
        assert task.job_id == "W-42"


class TestQuestionJobIdValidation:
    def test_question_rejects_invalid_job_id(self) -> None:
        with pytest.raises(ValueError):
            Question(
                question_id="Q-1",
                job_id="invalid",
                task_id="T-1",
                question="?",
                raised_by="backend",
            )

    def test_question_rejects_empty_job_id(self) -> None:
        with pytest.raises(ValueError):
            Question(
                question_id="Q-1",
                job_id="",
                task_id="T-1",
                question="?",
                raised_by="backend",
            )

    def test_question_rejects_wrong_prefix_job_id(self) -> None:
        with pytest.raises(ValueError):
            Question(
                question_id="Q-1",
                job_id="T-1",
                task_id="T-1",
                question="?",
                raised_by="backend",
            )

    def test_question_accepts_valid_job_id(self) -> None:
        q = Question(
            question_id="Q-1",
            job_id="W-99",
            task_id="T-1",
            question="?",
            raised_by="backend",
        )
        assert q.job_id == "W-99"


class TestQuestionModel:
    def test_create_question(self) -> None:
        question = Question(
            question_id="Q-1",
            job_id="W-1",
            task_id="T-2",
            question="Redis session store or JWT?",
            raised_by="backend",
        )
        assert question.question_id == "Q-1"
        assert question.status == QuestionStatus.RAISED
        assert question.answer is None

    def test_question_id_format(self) -> None:
        with pytest.raises(ValueError):
            Question(
                question_id="bad",
                job_id="W-1",
                task_id="T-1",
                question="?",
                raised_by="backend",
            )

    def test_question_display_id(self) -> None:
        q = Question(
            question_id="Q-3",
            job_id="W-1",
            task_id="T-2",
            question="?",
            raised_by="backend",
        )
        assert q.display_id == "W-1/Q-3"


class TestPRGroupModel:
    def test_create_pr_group(self) -> None:
        pr = PRGroup(
            branch_name="feat/user-auth",
            job_id="W-1",
            app="api-service",
            task_ids=["T-1", "T-2"],
        )
        assert pr.status == PRStatus.BRANCH_CREATED
        assert pr.pr_number is None

    def test_pr_group_requires_tasks(self) -> None:
        with pytest.raises(ValueError):
            PRGroup(
                branch_name="feat/empty",
                job_id="W-1",
                app="api-service",
                task_ids=[],
            )
