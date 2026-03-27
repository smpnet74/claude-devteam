"""Tests for structured output contracts."""

import json

import pytest
from devteam.agents.contracts import (
    DecompositionResult,
    EscalationAttemptResult,
    EscalationLevel,
    ImplementationResult,
    QuestionRecord,
    QuestionType,
    ReviewComment,
    ReviewResult,
    RoutePath,
    RoutingResult,
    TaskDecomposition,
    WorkType,
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


class TestReviewResultNeedsRevision:
    def test_needs_revision_true(self):
        comment = ReviewComment(file="x.py", line=1, severity="error", comment="bad")
        result = ReviewResult(verdict="needs_revision", comments=[comment], summary="fix it")
        assert result.needs_revision is True

    def test_blocked_needs_revision(self):
        comment = ReviewComment(file="x.py", line=1, severity="error", comment="blocked")
        result = ReviewResult(verdict="blocked", comments=[comment], summary="stuck")
        assert result.needs_revision is True

    def test_approved_no_revision(self):
        result = ReviewResult(verdict="approved", comments=[], summary="good")
        assert result.needs_revision is False

    def test_approved_with_comments_no_revision(self):
        comment = ReviewComment(file="x.py", line=1, severity="nitpick", comment="minor")
        result = ReviewResult(verdict="approved_with_comments", comments=[comment], summary="ok")
        assert result.needs_revision is False


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
            target_team="a",
        )
        assert result.path == "small_fix"
        assert result.target_team == "a"

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


class TestDependencyCycleDetection:
    """Cycle detection in DecompositionResult task graph."""

    def _make_task(self, task_id, depends_on=None):
        return TaskDecomposition(
            id=task_id,
            description=f"Task {task_id}",
            assigned_to="backend_engineer",
            team="a",
            depends_on=depends_on or [],
            pr_group="group-1",
        )

    def test_cycle_of_two_raises(self):
        t1 = self._make_task("T-1", depends_on=["T-2"])
        t2 = self._make_task("T-2", depends_on=["T-1"])
        with pytest.raises(ValueError, match="Dependency cycle detected"):
            DecompositionResult(tasks=[t1, t2])

    def test_cycle_of_three_raises(self):
        t1 = self._make_task("T-1", depends_on=["T-3"])
        t2 = self._make_task("T-2", depends_on=["T-1"])
        t3 = self._make_task("T-3", depends_on=["T-2"])
        with pytest.raises(ValueError, match="Dependency cycle detected"):
            DecompositionResult(tasks=[t1, t2, t3])

    def test_no_cycle_passes(self):
        t1 = self._make_task("T-1")
        t2 = self._make_task("T-2", depends_on=["T-1"])
        t3 = self._make_task("T-3", depends_on=["T-1", "T-2"])
        result = DecompositionResult(tasks=[t1, t2, t3])
        assert len(result.tasks) == 3


class TestSelfDependency:
    """Self-dependency check on TaskDecomposition."""

    def test_self_dependency_raises(self):
        with pytest.raises(ValueError, match="Task T-1 cannot depend on itself"):
            TaskDecomposition(
                id="T-1",
                description="test",
                assigned_to="backend_engineer",
                team="a",
                depends_on=["T-1"],
                pr_group="test",
            )


class TestEmptyPathValidation:
    """Validate files_changed/tests_added entries are not empty."""

    def test_empty_string_in_files_changed_raises(self):
        with pytest.raises(ValueError, match="File paths must not be empty"):
            ImplementationResult(
                status="completed",
                question=None,
                files_changed=["src/main.py", ""],
                tests_added=[],
                summary="Done",
                confidence="high",
            )

    def test_empty_string_in_tests_added_raises(self):
        with pytest.raises(ValueError, match="File paths must not be empty"):
            ImplementationResult(
                status="completed",
                question=None,
                files_changed=[],
                tests_added=["  "],
                summary="Done",
                confidence="high",
            )

    def test_whitespace_only_path_raises(self):
        with pytest.raises(ValueError, match="File paths must not be empty"):
            ImplementationResult(
                status="completed",
                question=None,
                files_changed=["   "],
                tests_added=[],
                summary="Done",
                confidence="high",
            )


class TestSchemaForRole:
    """Test the helper that maps roles to their output schema."""

    def test_all_schemas_are_valid_json_schema(self):
        for model_cls in [
            ImplementationResult,
            ReviewResult,
            DecompositionResult,
            RoutingResult,
            QuestionRecord,
        ]:
            schema = model_cls.model_json_schema()
            # Verify it's valid JSON by round-tripping
            json_str = json.dumps(schema)
            parsed = json.loads(json_str)
            assert parsed == schema


class TestRoutePath:
    """Tests for the RoutePath enum."""

    def test_all_values(self):
        assert RoutePath.FULL_PROJECT == "full_project"
        assert RoutePath.RESEARCH == "research"
        assert RoutePath.SMALL_FIX == "small_fix"
        assert RoutePath.OSS_CONTRIBUTION == "oss_contribution"

    def test_string_coercion_in_routing_result(self):
        """RoutePath is a str enum, so string values are accepted by Pydantic."""
        result = RoutingResult(path="full_project", reasoning="test")
        assert result.path == RoutePath.FULL_PROJECT
        assert isinstance(result.path, RoutePath)

    def test_invalid_path_rejected(self):
        with pytest.raises(ValueError):
            RoutingResult(path="nonexistent", reasoning="test")


class TestWorkType:
    """Tests for the WorkType enum."""

    def test_all_values(self):
        assert WorkType.CODE == "code"
        assert WorkType.RESEARCH == "research"
        assert WorkType.PLANNING == "planning"
        assert WorkType.ARCHITECTURE == "architecture"
        assert WorkType.DOCUMENTATION == "documentation"

    def test_default_work_type_on_task(self):
        task = TaskDecomposition(
            id="T-1",
            description="test task",
            assigned_to="backend_engineer",
            team="a",
            depends_on=[],
            pr_group="test",
        )
        assert task.work_type == WorkType.CODE

    def test_explicit_work_type(self):
        task = TaskDecomposition(
            id="T-1",
            description="research task",
            assigned_to="planner_researcher_a",
            team="a",
            depends_on=[],
            pr_group="research",
            work_type=WorkType.RESEARCH,
        )
        assert task.work_type == WorkType.RESEARCH

    def test_string_coercion(self):
        task = TaskDecomposition(
            id="T-1",
            description="docs task",
            assigned_to="tech_writer",
            team="b",
            depends_on=[],
            pr_group="docs",
            work_type="documentation",
        )
        assert task.work_type == WorkType.DOCUMENTATION


class TestQuestionType:
    """Tests for the QuestionType enum."""

    def test_all_values(self):
        assert QuestionType.TECHNICAL == "technical"
        assert QuestionType.ARCHITECTURAL == "architectural"
        assert QuestionType.PRODUCT == "product"
        assert QuestionType.PROCESS == "process"
        assert QuestionType.BLOCKED == "blocked"


class TestEscalationLevel:
    """Tests for the EscalationLevel enum."""

    def test_all_values(self):
        assert EscalationLevel.SUPERVISOR == "supervisor"
        assert EscalationLevel.LEADERSHIP == "leadership"
        assert EscalationLevel.HUMAN == "human"


class TestQuestionRecord:
    """Tests for the QuestionRecord model."""

    def test_basic_question(self):
        q = QuestionRecord(
            question="Should we use PostgreSQL or SQLite?",
            question_type=QuestionType.TECHNICAL,
        )
        assert q.question == "Should we use PostgreSQL or SQLite?"
        assert q.question_type == QuestionType.TECHNICAL
        assert q.escalation_level == EscalationLevel.SUPERVISOR
        assert q.context == ""

    def test_full_question(self):
        q = QuestionRecord(
            question="Is the auth module in scope?",
            question_type=QuestionType.PRODUCT,
            context="The spec mentions auth but the plan does not include it",
            escalation_level=EscalationLevel.LEADERSHIP,
        )
        assert q.question_type == QuestionType.PRODUCT
        assert q.escalation_level == EscalationLevel.LEADERSHIP
        assert "auth" in q.context

    def test_empty_question_rejected(self):
        with pytest.raises(ValueError):
            QuestionRecord(
                question="",
                question_type=QuestionType.TECHNICAL,
            )

    def test_string_coercion_for_enums(self):
        q = QuestionRecord(
            question="test question",
            question_type="architectural",
            escalation_level="human",
        )
        assert q.question_type == QuestionType.ARCHITECTURAL
        assert q.escalation_level == EscalationLevel.HUMAN

    def test_invalid_question_type_rejected(self):
        with pytest.raises(ValueError):
            QuestionRecord(
                question="test",
                question_type="invalid_type",
            )

    def test_invalid_escalation_level_rejected(self):
        with pytest.raises(ValueError):
            QuestionRecord(
                question="test",
                question_type=QuestionType.TECHNICAL,
                escalation_level="invalid_level",
            )


class TestRoutingResultWithEnum:
    """Tests for RoutingResult with RoutePath enum and target_team."""

    def test_with_route_path_enum(self):
        result = RoutingResult(
            path=RoutePath.FULL_PROJECT,
            reasoning="Complex multi-component feature",
        )
        assert result.path == RoutePath.FULL_PROJECT
        assert result.target_team is None

    def test_small_fix_with_target_team(self):
        result = RoutingResult(
            path=RoutePath.SMALL_FIX,
            reasoning="Single file bug fix",
            target_team="a",
        )
        assert result.path == RoutePath.SMALL_FIX
        assert result.target_team == "a"

    def test_oss_contribution(self):
        result = RoutingResult(
            path=RoutePath.OSS_CONTRIBUTION,
            reasoning="Contributing to external repo",
        )
        assert result.path == RoutePath.OSS_CONTRIBUTION

    def test_research_path(self):
        result = RoutingResult(
            path=RoutePath.RESEARCH,
            reasoning="User wants analysis, not code changes",
        )
        assert result.path == RoutePath.RESEARCH

    def test_json_roundtrip(self):
        original = RoutingResult(
            path=RoutePath.SMALL_FIX,
            reasoning="Quick fix",
            target_team="b",
        )
        data = json.loads(original.model_dump_json())
        restored = RoutingResult.model_validate(data)
        assert restored.path == original.path
        assert restored.target_team == original.target_team


class TestTargetTeamValidation:
    """Cross-field: target_team must be 'a' or 'b' for SMALL_FIX, None for others."""

    def test_small_fix_without_target_team_raises(self):
        with pytest.raises(ValueError, match="target_team must be 'a' or 'b'"):
            RoutingResult(path=RoutePath.SMALL_FIX, reasoning="test", target_team=None)

    def test_small_fix_with_invalid_target_team_raises(self):
        with pytest.raises(ValueError, match="target_team must be 'a' or 'b'"):
            RoutingResult(path=RoutePath.SMALL_FIX, reasoning="test", target_team="banana")

    def test_small_fix_with_team_a_succeeds(self):
        result = RoutingResult(path=RoutePath.SMALL_FIX, reasoning="test", target_team="a")
        assert result.target_team == "a"

    def test_small_fix_with_team_b_succeeds(self):
        result = RoutingResult(path=RoutePath.SMALL_FIX, reasoning="test", target_team="b")
        assert result.target_team == "b"

    def test_full_project_with_target_team_raises(self):
        with pytest.raises(ValueError, match="target_team must be None"):
            RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="test", target_team="a")

    def test_research_without_target_team_succeeds(self):
        result = RoutingResult(path=RoutePath.RESEARCH, reasoning="test")
        assert result.target_team is None

    def test_oss_contribution_with_target_team_raises(self):
        with pytest.raises(ValueError, match="target_team must be None"):
            RoutingResult(path=RoutePath.OSS_CONTRIBUTION, reasoning="test", target_team="a")


class TestParallelGroupSemanticValidation:
    """Validate parallel_groups semantic constraints in DecompositionResult."""

    def _make_task(self, task_id, depends_on=None):
        return TaskDecomposition(
            id=task_id,
            description=f"Task {task_id}",
            assigned_to="backend_engineer",
            team="a",
            depends_on=depends_on or [],
            pr_group="group-1",
        )

    def test_same_task_in_two_groups_raises(self):
        t1 = self._make_task("T-1")
        t2 = self._make_task("T-2")
        with pytest.raises(ValueError, match="T-1 appears in multiple parallel_groups"):
            DecompositionResult(
                tasks=[t1, t2],
                parallel_groups=[["T-1", "T-2"], ["T-1"]],
            )

    def test_dependent_tasks_in_same_group_raises(self):
        t1 = self._make_task("T-1")
        t2 = self._make_task("T-2", depends_on=["T-1"])
        with pytest.raises(ValueError, match="T-2 and T-1 are in the same parallel_group"):
            DecompositionResult(
                tasks=[t1, t2],
                parallel_groups=[["T-1", "T-2"]],
            )

    def test_independent_tasks_in_same_group_ok(self):
        t1 = self._make_task("T-1")
        t2 = self._make_task("T-2")
        result = DecompositionResult(
            tasks=[t1, t2],
            parallel_groups=[["T-1", "T-2"]],
        )
        assert len(result.parallel_groups) == 1


class TestEscalationAttemptResult:
    """Tests for the EscalationAttemptResult structured output contract."""

    def test_resolved_with_answer(self):
        result = EscalationAttemptResult(
            resolved=True,
            answer="Use Redis",
            reasoning="Matches existing stack",
        )
        assert result.resolved
        assert result.answer == "Use Redis"

    def test_unresolved_no_answer(self):
        result = EscalationAttemptResult(
            resolved=False,
            reasoning="Need more context from product team",
        )
        assert not result.resolved
        assert result.answer is None

    def test_empty_reasoning_rejected(self):
        with pytest.raises(ValueError):
            EscalationAttemptResult(
                resolved=True,
                answer="Use Redis",
                reasoning="",
            )

    def test_missing_reasoning_rejected(self):
        with pytest.raises(ValueError):
            EscalationAttemptResult(resolved=True, answer="Use Redis")

    def test_json_schema_generation(self):
        schema = EscalationAttemptResult.model_json_schema()
        assert "properties" in schema
        assert "resolved" in schema["properties"]
        assert "answer" in schema["properties"]
        assert "reasoning" in schema["properties"]

    def test_model_validate_from_dict(self):
        data = {
            "resolved": True,
            "answer": "Use Postgres",
            "reasoning": "Better for our needs",
        }
        result = EscalationAttemptResult.model_validate(data)
        assert result.resolved
        assert result.answer == "Use Postgres"

    def test_resolved_true_answer_none_raises(self):
        with pytest.raises(ValueError, match="answer is required when resolved=True"):
            EscalationAttemptResult(resolved=True, answer=None, reasoning="some reasoning")

    def test_resolved_true_answer_empty_raises(self):
        with pytest.raises(ValueError, match="answer is required when resolved=True"):
            EscalationAttemptResult(resolved=True, answer="", reasoning="some reasoning")

    def test_resolved_true_answer_present_succeeds(self):
        result = EscalationAttemptResult(
            resolved=True, answer="use JWT", reasoning="some reasoning"
        )
        assert result.resolved
        assert result.answer == "use JWT"

    def test_resolved_false_answer_none_succeeds(self):
        result = EscalationAttemptResult(resolved=False, answer=None, reasoning="some reasoning")
        assert not result.resolved
        assert result.answer is None
