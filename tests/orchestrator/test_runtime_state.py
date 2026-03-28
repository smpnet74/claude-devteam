"""Tests for durable runtime state store."""

import time

import pytest

from devteam.orchestrator.runtime_state import (
    ArtifactRecord,
    JobRecord,
    QuestionRecord,
    RuntimeStateStore,
    TaskRecord,
)


@pytest.fixture
def store(tmp_path):
    s = RuntimeStateStore(str(tmp_path / "runtime.sqlite"))
    yield s
    s.close()


class TestJobRegistry:
    def test_register_and_get(self, store):
        job = store.register_job(
            workflow_id="uuid-123",
            project_name="myproject",
            repo_root="/home/user/myproject",
        )
        assert job.alias == "W-1"
        assert job.workflow_id == "uuid-123"

        fetched = store.get_job("W-1")
        assert fetched is not None
        assert fetched.workflow_id == "uuid-123"

    def test_sequential_aliases(self, store):
        j1 = store.register_job(workflow_id="a", project_name="p", repo_root="/r")
        j2 = store.register_job(workflow_id="b", project_name="p", repo_root="/r")
        assert j1.alias == "W-1"
        assert j2.alias == "W-2"

    def test_aliases_survive_reopen(self, tmp_path):
        db_path = str(tmp_path / "survive.sqlite")
        s1 = RuntimeStateStore(db_path)
        s1.register_job(workflow_id="a", project_name="p", repo_root="/r")
        s1.close()

        s2 = RuntimeStateStore(db_path)
        j = s2.register_job(workflow_id="b", project_name="p", repo_root="/r")
        assert j.alias == "W-2"
        s2.close()

    def test_get_by_workflow_id(self, store):
        store.register_job(workflow_id="uuid-abc", project_name="p", repo_root="/r")
        job = store.get_job_by_workflow_id("uuid-abc")
        assert job is not None
        assert job.alias == "W-1"

    def test_update_status(self, store):
        store.register_job(workflow_id="a", project_name="p", repo_root="/r")
        store.update_job_status("W-1", "completed")
        job = store.get_job("W-1")
        assert job.status == "completed"

    def test_get_active_jobs(self, store):
        store.register_job(workflow_id="a", project_name="p", repo_root="/r")
        store.register_job(workflow_id="b", project_name="p", repo_root="/r")
        store.update_job_status("W-1", "completed")
        active = store.get_active_jobs()
        assert len(active) == 1
        assert active[0].alias == "W-2"


class TestTaskRegistry:
    def test_register_and_get(self, store):
        store.register_job(workflow_id="parent", project_name="p", repo_root="/r")
        task = store.register_task(
            alias="T-1",
            workflow_id="child-uuid",
            job_alias="W-1",
            assigned_to="backend_engineer",
        )
        assert task.alias == "T-1"
        fetched = store.get_task("T-1")
        assert fetched is not None
        assert fetched.workflow_id == "child-uuid"

    def test_get_tasks_for_job(self, store):
        store.register_job(workflow_id="parent", project_name="p", repo_root="/r")
        store.register_task(
            alias="T-1", workflow_id="c1", job_alias="W-1", assigned_to="backend_engineer"
        )
        store.register_task(
            alias="T-2", workflow_id="c2", job_alias="W-1", assigned_to="frontend_engineer"
        )
        tasks = store.get_tasks_for_job("W-1")
        assert len(tasks) == 2


class TestQuestionRegistry:
    def test_register_and_lookup(self, store):
        display = store.register_question(
            internal_id="Q-T2-1",
            child_workflow_id="child-uuid",
            task_alias="T-2",
            text="Redis or JWT?",
            tier=2,
        )
        assert display == "Q-1"
        q = store.lookup_question("Q-1")
        assert q is not None
        assert q.internal_id == "Q-T2-1"
        assert q.child_workflow_id == "child-uuid"

    def test_resolve(self, store):
        store.register_question(
            internal_id="Q-T2-1",
            child_workflow_id="c",
            task_alias="T-2",
            text="Redis or JWT?",
            tier=2,
        )
        q = store.resolve_question("Q-1")
        assert q is not None
        assert q.resolved is True
        q2 = store.lookup_question("Q-1")
        assert q2.resolved is True

    def test_get_pending(self, store):
        store.register_question(
            internal_id="a", child_workflow_id="c1", task_alias="T-1", text="Q1", tier=2
        )
        store.register_question(
            internal_id="b", child_workflow_id="c2", task_alias="T-2", text="Q2", tier=1
        )
        store.resolve_question("Q-1")
        pending = store.get_pending_questions()
        assert len(pending) == 1
        assert pending[0].display_alias == "Q-2"


class TestArtifactRegistry:
    def test_register_and_get(self, store):
        store.register_job(workflow_id="p", project_name="p", repo_root="/r")
        store.register_task(alias="T-1", workflow_id="c", job_alias="W-1", assigned_to="be")
        store.register_artifact(
            task_alias="T-1", worktree_path="/wt/T-1", branch_name="devteam/feat/T-1"
        )
        art = store.get_artifact("T-1")
        assert art is not None
        assert art.worktree_path == "/wt/T-1"
        assert art.branch_name == "devteam/feat/T-1"

    def test_update_pr(self, store):
        store.register_job(workflow_id="p", project_name="p", repo_root="/r")
        store.register_task(alias="T-1", workflow_id="c", job_alias="W-1", assigned_to="be")
        store.register_artifact(task_alias="T-1", worktree_path="/wt", branch_name="b")
        store.update_pr(
            task_alias="T-1", pr_number=42, pr_url="https://github.com/x/y/pull/42", pr_state="open"
        )
        art = store.get_artifact("T-1")
        assert art.pr_number == 42
        assert art.pr_state == "open"

    def test_get_artifacts_for_job(self, store):
        store.register_job(workflow_id="p", project_name="p", repo_root="/r")
        store.register_task(alias="T-1", workflow_id="c1", job_alias="W-1", assigned_to="be")
        store.register_task(alias="T-2", workflow_id="c2", job_alias="W-1", assigned_to="fe")
        store.register_artifact(task_alias="T-1", worktree_path="/wt1", branch_name="b1")
        store.register_artifact(task_alias="T-2", worktree_path="/wt2", branch_name="b2")
        artifacts = store.get_artifacts_for_job("W-1")
        assert len(artifacts) == 2
