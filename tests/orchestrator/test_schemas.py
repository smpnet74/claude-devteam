"""Tests for the orchestrator.schemas re-export layer."""

import json

from devteam.orchestrator.schemas import (
    DecompositionResult,
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


class TestSchemasReExports:
    """Verify all expected types are importable from orchestrator.schemas."""

    def test_all_expected_types_importable(self):
        expected = [
            DecompositionResult,
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
        ]
        for cls in expected:
            assert cls is not None

    def test_types_are_same_as_contracts(self):
        """Re-exports should be the exact same classes, not copies."""
        from devteam.agents.contracts import (
            DecompositionResult as DR,
            RoutingResult as RR,
            TaskDecomposition as TD,
        )

        assert DecompositionResult is DR
        assert RoutingResult is RR
        assert TaskDecomposition is TD


class TestSchemasJsonGeneration:
    """Test JSON schema generation through the re-export layer."""

    def test_routing_result_schema(self):
        schema = RoutingResult.model_json_schema()
        assert "properties" in schema
        assert "path" in schema["properties"]
        assert "reasoning" in schema["properties"]
        # Roundtrip through JSON
        json_str = json.dumps(schema)
        parsed = json.loads(json_str)
        assert parsed == schema

    def test_decomposition_result_schema(self):
        schema = DecompositionResult.model_json_schema()
        assert "properties" in schema
        assert "tasks" in schema["properties"]
        assert "peer_assignments" in schema["properties"]
        assert "parallel_groups" in schema["properties"]

    def test_implementation_result_schema(self):
        schema = ImplementationResult.model_json_schema()
        assert "properties" in schema
        assert "status" in schema["properties"]
        assert "files_changed" in schema["properties"]


class TestSchemasValidatorsThroughReExport:
    """Test that validators work correctly through the re-export layer."""

    def test_route_path_enum_via_schemas(self):
        result = RoutingResult(
            path=RoutePath.FULL_PROJECT,
            reasoning="test via schemas module",
        )
        assert result.path == RoutePath.FULL_PROJECT

    def test_task_id_validation_via_schemas(self):
        task = TaskDecomposition(
            id="T-1",
            description="test",
            assigned_to="backend_engineer",
            team="a",
            depends_on=[],
            pr_group="test",
        )
        assert task.id == "T-1"

    def test_work_type_default_via_schemas(self):
        task = TaskDecomposition(
            id="T-1",
            description="test",
            assigned_to="backend_engineer",
            team="a",
            depends_on=[],
            pr_group="test",
        )
        assert task.work_type == WorkType.CODE
