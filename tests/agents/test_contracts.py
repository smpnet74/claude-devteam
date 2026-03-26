"""Tests for structured output contracts."""

import json

import pytest
from devteam.agents.contracts import (
    DecompositionResult,
    ImplementationResult,
    ReviewComment,
    ReviewResult,
    RoutingResult,
    TaskDecomposition,
)


class TestImplementationResult:
    def test_completed_result(self):
        result = ImplementationResult(
            status="completed",
            question=None,
            files_changed=["src/main.py", "src/utils.py"],
            tests_added=["tests/test_main.py"],
            summary="Implemented user authentication flow",
            confidence="high",
        )
        assert result.status == "completed"
        assert result.question is None
        assert len(result.files_changed) == 2
        assert result.confidence == "high"

    def test_needs_clarification_requires_question(self):
        result = ImplementationResult(
            status="needs_clarification",
            question="Should auth use OAuth2 or API keys?",
            files_changed=[],
            tests_added=[],
            summary="Blocked on auth strategy decision",
            confidence="low",
        )
        assert result.status == "needs_clarification"
        assert result.question is not None

    def test_blocked_result(self):
        result = ImplementationResult(
            status="blocked",
            question="Database migration failed -- need DBA help",
            files_changed=[],
            tests_added=[],
            summary="Migration blocked",
            confidence="low",
        )
        assert result.status == "blocked"

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError):
            ImplementationResult(
                status="invalid_status",
                question=None,
                files_changed=[],
                tests_added=[],
                summary="test",
                confidence="high",
            )

    def test_invalid_confidence_rejected(self):
        with pytest.raises(ValueError):
            ImplementationResult(
                status="completed",
                question=None,
                files_changed=[],
                tests_added=[],
                summary="test",
                confidence="very_high",
            )

    def test_json_schema_generation(self):
        schema = ImplementationResult.model_json_schema()
        assert "properties" in schema
        assert "status" in schema["properties"]
        assert "files_changed" in schema["properties"]
        # Ensure enum constraints are present
        status_schema = schema["properties"]["status"]
        assert "enum" in status_schema or "$ref" in status_schema or "anyOf" in status_schema

    def test_empty_summary_rejected(self):
        with pytest.raises(ValueError):
            ImplementationResult(
                status="completed",
                question=None,
                files_changed=[],
                tests_added=[],
                summary="",
                confidence="high",
            )


class TestReviewResult:
    def test_approved_no_comments(self):
        result = ReviewResult(
            verdict="approved",
            comments=[],
            summary="Code looks good, well-structured.",
        )
        assert result.verdict == "approved"
        assert len(result.comments) == 0

    def test_needs_revision_with_comments(self):
        result = ReviewResult(
            verdict="needs_revision",
            comments=[
                ReviewComment(
                    file="src/main.py",
                    line=42,
                    severity="error",
                    comment="Missing null check on user input",
                ),
                ReviewComment(
                    file="src/main.py",
                    line=78,
                    severity="nitpick",
                    comment="Consider renaming variable for clarity",
                ),
            ],
            summary="One critical issue found.",
        )
        assert result.verdict == "needs_revision"
        assert len(result.comments) == 2
        assert result.comments[0].severity == "error"

    def test_invalid_verdict_rejected(self):
        with pytest.raises(ValueError):
            ReviewResult(
                verdict="maybe",
                comments=[],
                summary="test",
            )

    def test_invalid_severity_rejected(self):
        with pytest.raises(ValueError):
            ReviewComment(
                file="test.py",
                line=1,
                severity="critical",
                comment="test",
            )

    def test_json_schema_generation(self):
        schema = ReviewResult.model_json_schema()
        assert "properties" in schema
        assert "verdict" in schema["properties"]
        assert "comments" in schema["properties"]

    def test_invalid_line_number_rejected(self):
        with pytest.raises(ValueError):
            ReviewComment(
                file="test.py",
                line=0,
                severity="error",
                comment="test",
            )

    def test_empty_file_path_rejected(self):
        with pytest.raises(ValueError):
            ReviewComment(
                file="",
                line=1,
                severity="error",
                comment="test",
            )


class TestReviewResultVerdictCommentsValidation:
    """Cross-field: verdicts that imply comments must have them."""

    def test_approved_with_comments_no_comments_raises(self):
        with pytest.raises(ValueError, match="approved_with_comments.*requires at least one"):
            ReviewResult(
                verdict="approved_with_comments",
                comments=[],
                summary="Looks good with minor notes",
            )

    def test_needs_revision_no_comments_raises(self):
        with pytest.raises(ValueError, match="needs_revision.*requires at least one"):
            ReviewResult(
                verdict="needs_revision",
                comments=[],
                summary="Issues found",
            )

    def test_blocked_no_comments_raises(self):
        with pytest.raises(ValueError, match="blocked.*requires at least one comment"):
            ReviewResult(
                verdict="blocked",
                comments=[],
                summary="Cannot proceed",
            )

    def test_approved_with_comments_with_comments_ok(self):
        result = ReviewResult(
            verdict="approved_with_comments",
            comments=[
                ReviewComment(
                    file="src/main.py",
                    line=10,
                    severity="nitpick",
                    comment="Minor style issue",
                ),
            ],
            summary="Approved with minor notes",
        )
        assert result.verdict == "approved_with_comments"

    def test_blocked_with_comments_ok(self):
        result = ReviewResult(
            verdict="blocked",
            comments=[
                ReviewComment(
                    file="src/main.py",
                    line=1,
                    severity="error",
                    comment="Security vulnerability",
                ),
            ],
            summary="Blocked due to security issue",
        )
        assert result.verdict == "blocked"


class TestDecompositionResult:
    def test_simple_decomposition(self):
        result = DecompositionResult(
            tasks=[
                TaskDecomposition(
                    id="T-1",
                    description="Set up database schema",
                    assigned_to="data_engineer",
                    team="b",
                    depends_on=[],
                    pr_group="database-setup",
                ),
                TaskDecomposition(
                    id="T-2",
                    description="Build REST API endpoints",
                    assigned_to="backend_engineer",
                    team="a",
                    depends_on=["T-1"],
                    pr_group="api-endpoints",
                ),
            ],
            peer_assignments={"T-1": "infra_engineer", "T-2": "frontend_engineer"},
            parallel_groups=[["T-1"], ["T-2"]],
        )
        assert len(result.tasks) == 2
        assert result.tasks[1].depends_on == ["T-1"]
        assert result.peer_assignments["T-1"] == "infra_engineer"

    def test_invalid_team_rejected(self):
        with pytest.raises(ValueError):
            TaskDecomposition(
                id="T-1",
                description="test",
                assigned_to="backend_engineer",
                team="c",
                depends_on=[],
                pr_group="test",
            )

    def test_json_schema_generation(self):
        schema = DecompositionResult.model_json_schema()
        assert "properties" in schema
        assert "tasks" in schema["properties"]

    def test_empty_task_id_rejected(self):
        with pytest.raises(ValueError):
            TaskDecomposition(
                id="",
                description="test",
                assigned_to="backend_engineer",
                team="a",
                depends_on=[],
                pr_group="test",
            )


class TestDecompositionResultGraphValidation:
    """Cross-field: task graph integrity checks."""

    def _make_task(self, task_id, depends_on=None):
        return TaskDecomposition(
            id=task_id,
            description=f"Task {task_id}",
            assigned_to="backend_engineer",
            team="a",
            depends_on=depends_on or [],
            pr_group="group-1",
        )

    def test_duplicate_task_ids_raises(self):
        t1a = self._make_task("T-1")
        t1b = self._make_task("T-1")
        with pytest.raises(ValueError, match="Duplicate task IDs"):
            DecompositionResult(tasks=[t1a, t1b])

    def test_depends_on_unknown_task_raises(self):
        t1 = self._make_task("T-1", depends_on=["T-99"])
        with pytest.raises(ValueError, match="depends on unknown task T-99"):
            DecompositionResult(tasks=[t1])

    def test_bad_peer_assignments_raises(self):
        t1 = self._make_task("T-1")
        with pytest.raises(ValueError, match="peer_assignments references unknown task T-99"):
            DecompositionResult(
                tasks=[t1],
                peer_assignments={"T-99": "qa_engineer"},
            )

    def test_bad_parallel_groups_raises(self):
        t1 = self._make_task("T-1")
        with pytest.raises(ValueError, match="parallel_groups references unknown task T-99"):
            DecompositionResult(
                tasks=[t1],
                parallel_groups=[["T-99"]],
            )

    def test_valid_graph_passes(self):
        t1 = self._make_task("T-1")
        t2 = self._make_task("T-2", depends_on=["T-1"])
        result = DecompositionResult(
            tasks=[t1, t2],
            peer_assignments={"T-1": "qa_engineer"},
            parallel_groups=[["T-1"], ["T-2"]],
        )
        assert len(result.tasks) == 2


class TestRoutingResult:
    def test_full_project_routing(self):
        result = RoutingResult(
            path="full_project",
            reasoning="Complex multi-component feature requiring architecture review",
        )
        assert result.path == "full_project"

    def test_small_fix_routing(self):
        result = RoutingResult(
            path="small_fix",
            reasoning="Single file bug fix with clear scope",
        )
        assert result.path == "small_fix"

    def test_invalid_path_rejected(self):
        with pytest.raises(ValueError):
            RoutingResult(
                path="unknown_path",
                reasoning="test",
            )

    def test_json_schema_generation(self):
        schema = RoutingResult.model_json_schema()
        assert "properties" in schema
        assert "path" in schema["properties"]

    def test_empty_reasoning_rejected(self):
        with pytest.raises(ValueError):
            RoutingResult(
                path="full_project",
                reasoning="",
            )


class TestTaskDecompositionValidation:
    def test_valid_task_id(self):
        task = TaskDecomposition(
            id="T-1",
            description="test",
            assigned_to="backend_engineer",
            team="a",
            depends_on=[],
            pr_group="test",
        )
        assert task.id == "T-1"

    def test_multi_digit_task_id(self):
        task = TaskDecomposition(
            id="T-42",
            description="test",
            assigned_to="backend_engineer",
            team="a",
            depends_on=[],
            pr_group="test",
        )
        assert task.id == "T-42"

    def test_invalid_task_id_format_rejected(self):
        with pytest.raises(ValueError, match="T-<n>"):
            TaskDecomposition(
                id="task-1",
                description="test",
                assigned_to="backend_engineer",
                team="a",
                depends_on=[],
                pr_group="test",
            )

    def test_task_id_zero_rejected(self):
        with pytest.raises(ValueError, match="T-<n>"):
            TaskDecomposition(
                id="T-0",
                description="test",
                assigned_to="backend_engineer",
                team="a",
                depends_on=[],
                pr_group="test",
            )

    def test_valid_depends_on(self):
        task = TaskDecomposition(
            id="T-2",
            description="test",
            assigned_to="backend_engineer",
            team="a",
            depends_on=["T-1"],
            pr_group="test",
        )
        assert task.depends_on == ["T-1"]

    def test_invalid_depends_on_rejected(self):
        with pytest.raises(ValueError, match="depends_on"):
            TaskDecomposition(
                id="T-2",
                description="test",
                assigned_to="backend_engineer",
                team="a",
                depends_on=["bad-id"],
                pr_group="test",
            )


class TestImplementationResultCrossField:
    def test_blocked_without_question_rejected(self):
        with pytest.raises(ValueError, match="question.*required"):
            ImplementationResult(
                status="blocked",
                question=None,
                files_changed=[],
                tests_added=[],
                summary="Blocked on dependency",
                confidence="low",
            )

    def test_needs_clarification_without_question_rejected(self):
        with pytest.raises(ValueError, match="question.*required"):
            ImplementationResult(
                status="needs_clarification",
                question=None,
                files_changed=[],
                tests_added=[],
                summary="Need more info",
                confidence="low",
            )

    def test_completed_without_question_ok(self):
        result = ImplementationResult(
            status="completed",
            question=None,
            files_changed=[],
            tests_added=[],
            summary="Done",
            confidence="high",
        )
        assert result.question is None


class TestDecompositionResultEmptyTasks:
    def test_empty_tasks_rejected(self):
        with pytest.raises(ValueError):
            DecompositionResult(
                tasks=[],
                peer_assignments={},
                parallel_groups=[],
            )


class TestSchemaForRole:
    """Test the helper that maps roles to their output schema."""

    def test_all_schemas_are_valid_json_schema(self):
        for model_cls in [
            ImplementationResult,
            ReviewResult,
            DecompositionResult,
            RoutingResult,
        ]:
            schema = model_cls.model_json_schema()
            # Verify it's valid JSON by round-tripping
            json_str = json.dumps(schema)
            parsed = json.loads(json_str)
            assert parsed == schema
