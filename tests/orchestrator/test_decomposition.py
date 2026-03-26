"""Tests for CA decomposition workflow."""

from unittest.mock import MagicMock

import pytest

from devteam.orchestrator.decomposition import (
    assign_peer_reviewers,
    build_decomposition_prompt,
    decompose,
    get_default_peer_reviewer,
    validate_decomposition,
)
from devteam.orchestrator.schemas import (
    DecompositionResult,
    RoutePath,
    RoutingResult,
    TaskDecomposition,
    WorkType,
)


def _make_task(
    id: str,
    assigned_to: str,
    team: str,
    depends_on: list[str] | None = None,
    work_type: WorkType = WorkType.CODE,
) -> TaskDecomposition:
    return TaskDecomposition(
        id=id,
        description=f"Task {id}",
        assigned_to=assigned_to,
        team=team,
        depends_on=depends_on or [],
        pr_group="feat/main",
        work_type=work_type,
    )


class TestGetDefaultPeerReviewer:
    def test_backend_reviewed_by_frontend(self):
        assert get_default_peer_reviewer("backend", "a") == "frontend"

    def test_frontend_reviewed_by_backend(self):
        assert get_default_peer_reviewer("frontend", "a") == "backend"

    def test_devops_reviewed_by_backend(self):
        assert get_default_peer_reviewer("devops", "a") == "backend"

    def test_data_reviewed_by_infra(self):
        assert get_default_peer_reviewer("data", "b") == "infra"

    def test_infra_reviewed_by_data(self):
        assert get_default_peer_reviewer("infra", "b") == "data"

    def test_tooling_reviewed_by_infra(self):
        assert get_default_peer_reviewer("tooling", "b") == "infra"

    def test_cloud_reviewed_by_infra(self):
        assert get_default_peer_reviewer("cloud", "b") == "infra"

    def test_unknown_role_returns_none(self):
        assert get_default_peer_reviewer("ceo", "a") is None

    def test_unknown_team_returns_none(self):
        assert get_default_peer_reviewer("backend", "c") is None

    def test_role_not_in_team_b_returns_none(self):
        assert get_default_peer_reviewer("backend", "b") is None

    def test_role_not_in_team_a_returns_none(self):
        assert get_default_peer_reviewer("data", "a") is None


class TestAssignPeerReviewers:
    def test_fills_defaults(self):
        tasks = [_make_task("T-1", "backend", "a")]
        assignments = assign_peer_reviewers(tasks)
        assert assignments == {"T-1": "frontend"}

    def test_explicit_overrides_default(self):
        tasks = [_make_task("T-1", "backend", "a")]
        assignments = assign_peer_reviewers(tasks, {"T-1": "devops"})
        assert assignments == {"T-1": "devops"}

    def test_multiple_tasks(self):
        tasks = [
            _make_task("T-1", "backend", "a"),
            _make_task("T-2", "data", "b"),
        ]
        assignments = assign_peer_reviewers(tasks)
        assert assignments == {"T-1": "frontend", "T-2": "infra"}

    def test_no_reviewer_for_unknown_role(self):
        tasks = [_make_task("T-1", "ceo", "a")]
        assignments = assign_peer_reviewers(tasks)
        assert assignments == {}

    def test_preserves_explicit_and_fills_missing(self):
        tasks = [
            _make_task("T-1", "backend", "a"),
            _make_task("T-2", "frontend", "a"),
        ]
        assignments = assign_peer_reviewers(tasks, {"T-1": "devops"})
        assert assignments == {"T-1": "devops", "T-2": "backend"}

    def test_empty_tasks(self):
        assignments = assign_peer_reviewers([])
        assert assignments == {}


class TestBuildDecompositionPrompt:
    def test_includes_spec_and_plan(self):
        routing = RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="test reason")
        prompt = build_decomposition_prompt("my spec", "my plan", routing)
        assert "my spec" in prompt
        assert "my plan" in prompt

    def test_includes_routing_decision(self):
        routing = RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="Complex feature")
        prompt = build_decomposition_prompt("spec", "plan", routing)
        assert "full_project" in prompt
        assert "Complex feature" in prompt

    def test_includes_instructions(self):
        routing = RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="test")
        prompt = build_decomposition_prompt("spec", "plan", routing)
        assert "engineer role" in prompt
        assert "dependencies" in prompt
        assert "PR groups" in prompt
        assert "parallel" in prompt.lower()


class TestValidateDecomposition:
    """validate_decomposition provides a second layer of validation
    for post-processed results. It catches the same errors as Pydantic
    but returns a list of strings rather than raising.

    Note: We use model_construct() to bypass Pydantic validators when
    we need to test validate_decomposition with invalid data that Pydantic
    would reject during normal construction.
    """

    def test_valid_decomposition(self):
        result = DecompositionResult(
            tasks=[
                _make_task("T-1", "backend", "a"),
                _make_task("T-2", "frontend", "a", depends_on=["T-1"]),
            ],
            peer_assignments={"T-1": "frontend", "T-2": "backend"},
            parallel_groups=[["T-1"], ["T-2"]],
        )
        assert validate_decomposition(result) == []

    def test_unknown_dependency(self):
        # Use model_construct to bypass Pydantic validation
        t1 = _make_task("T-1", "backend", "a")
        t1_bad = t1.model_copy(update={"depends_on": ["T-99"]})
        result = DecompositionResult.model_construct(
            tasks=[t1_bad],
            peer_assignments={},
            parallel_groups=[],
        )
        errors = validate_decomposition(result)
        assert any("unknown task T-99" in e for e in errors)

    def test_unknown_peer_assignment(self):
        result = DecompositionResult.model_construct(
            tasks=[_make_task("T-1", "backend", "a")],
            peer_assignments={"T-99": "frontend"},
            parallel_groups=[],
        )
        errors = validate_decomposition(result)
        assert any("unknown task T-99" in e for e in errors)

    def test_circular_dependency(self):
        t1 = _make_task("T-1", "backend", "a")
        t2 = _make_task("T-2", "frontend", "a")
        t1_cycle = t1.model_copy(update={"depends_on": ["T-2"]})
        t2_cycle = t2.model_copy(update={"depends_on": ["T-1"]})
        result = DecompositionResult.model_construct(
            tasks=[t1_cycle, t2_cycle],
            peer_assignments={},
            parallel_groups=[],
        )
        errors = validate_decomposition(result)
        assert any("Circular" in e for e in errors)

    def test_unknown_parallel_group_task(self):
        result = DecompositionResult.model_construct(
            tasks=[_make_task("T-1", "backend", "a")],
            peer_assignments={},
            parallel_groups=[["T-1", "T-99"]],
        )
        errors = validate_decomposition(result)
        assert any("T-99" in e for e in errors)

    def test_single_task_no_deps_valid(self):
        result = DecompositionResult(
            tasks=[_make_task("T-1", "backend", "a")],
            peer_assignments={},
            parallel_groups=[],
        )
        assert validate_decomposition(result) == []

    def test_complex_valid_dag(self):
        """A diamond-shaped DAG: T-1 -> T-2, T-3 -> T-4."""
        result = DecompositionResult(
            tasks=[
                _make_task("T-1", "backend", "a"),
                _make_task("T-2", "frontend", "a", depends_on=["T-1"]),
                _make_task("T-3", "devops", "a", depends_on=["T-1"]),
                _make_task("T-4", "backend", "a", depends_on=["T-2", "T-3"]),
            ],
            peer_assignments={},
            parallel_groups=[["T-2", "T-3"]],
        )
        assert validate_decomposition(result) == []

    def test_pydantic_catches_unknown_dependency(self):
        """Pydantic model validators also catch unknown deps at construction."""
        with pytest.raises(ValueError, match="depends on unknown task"):
            DecompositionResult(
                tasks=[_make_task("T-1", "backend", "a", depends_on=["T-99"])],
                peer_assignments={},
                parallel_groups=[],
            )

    def test_pydantic_catches_circular_dependency(self):
        """Pydantic model validators also catch cycles at construction."""
        with pytest.raises(ValueError, match="Dependency cycle detected"):
            DecompositionResult(
                tasks=[
                    _make_task("T-1", "backend", "a", depends_on=["T-2"]),
                    _make_task("T-2", "frontend", "a", depends_on=["T-1"]),
                ],
                peer_assignments={},
                parallel_groups=[],
            )


class TestDecompose:
    def test_invokes_ca_and_fills_peers(self):
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "tasks": [
                {
                    "id": "T-1",
                    "description": "Build API",
                    "assigned_to": "backend",
                    "team": "a",
                    "depends_on": [],
                    "pr_group": "feat/api",
                    "work_type": "code",
                },
            ],
            "peer_assignments": {},
            "parallel_groups": [["T-1"]],
        }
        routing = RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="test")
        result = decompose("spec", "plan", routing, invoker)

        assert len(result.tasks) == 1
        assert result.peer_assignments["T-1"] == "frontend"
        invoker.invoke.assert_called_once()

    def test_invalid_ca_output_raises(self):
        """Invalid CA output (unknown dependency) is caught by Pydantic."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "tasks": [
                {
                    "id": "T-1",
                    "description": "Build API",
                    "assigned_to": "backend",
                    "team": "a",
                    "depends_on": ["T-99"],
                    "pr_group": "feat/api",
                    "work_type": "code",
                },
            ],
            "peer_assignments": {},
            "parallel_groups": [],
        }
        routing = RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="test")
        with pytest.raises(ValueError, match="depends on unknown task T-99"):
            decompose("spec", "plan", routing, invoker)

    def test_explicit_peer_assignments_preserved(self):
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "tasks": [
                {
                    "id": "T-1",
                    "description": "Build API",
                    "assigned_to": "backend",
                    "team": "a",
                    "depends_on": [],
                    "pr_group": "feat/api",
                },
            ],
            "peer_assignments": {"T-1": "devops"},
            "parallel_groups": [["T-1"]],
        }
        routing = RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="test")
        result = decompose("spec", "plan", routing, invoker)

        # Explicit assignment should override default
        assert result.peer_assignments["T-1"] == "devops"

    def test_invokes_with_correct_role(self):
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "tasks": [
                {
                    "id": "T-1",
                    "description": "Build API",
                    "assigned_to": "backend",
                    "team": "a",
                    "depends_on": [],
                    "pr_group": "feat/api",
                },
            ],
            "peer_assignments": {},
            "parallel_groups": [],
        }
        routing = RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="test")
        decompose("spec", "plan", routing, invoker)

        call_kwargs = invoker.invoke.call_args.kwargs
        assert call_kwargs["role"] == "chief_architect"

    def test_multi_task_decomposition(self):
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "tasks": [
                {
                    "id": "T-1",
                    "description": "Set up database",
                    "assigned_to": "data",
                    "team": "b",
                    "depends_on": [],
                    "pr_group": "database",
                },
                {
                    "id": "T-2",
                    "description": "Build API",
                    "assigned_to": "backend",
                    "team": "a",
                    "depends_on": ["T-1"],
                    "pr_group": "api",
                },
                {
                    "id": "T-3",
                    "description": "Build UI",
                    "assigned_to": "frontend",
                    "team": "a",
                    "depends_on": ["T-2"],
                    "pr_group": "ui",
                },
            ],
            "peer_assignments": {},
            "parallel_groups": [["T-1"], ["T-2"], ["T-3"]],
        }
        routing = RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="test")
        result = decompose("spec", "plan", routing, invoker)

        assert len(result.tasks) == 3
        assert result.peer_assignments["T-1"] == "infra"  # data -> infra (team b)
        assert result.peer_assignments["T-2"] == "frontend"  # backend -> frontend (team a)
        assert result.peer_assignments["T-3"] == "backend"  # frontend -> backend (team a)

    def test_work_type_preserved(self):
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "tasks": [
                {
                    "id": "T-1",
                    "description": "Research auth options",
                    "assigned_to": "backend",
                    "team": "a",
                    "depends_on": [],
                    "pr_group": "research",
                    "work_type": "research",
                },
            ],
            "peer_assignments": {},
            "parallel_groups": [],
        }
        routing = RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="test")
        result = decompose("spec", "plan", routing, invoker)

        assert result.tasks[0].work_type == WorkType.RESEARCH


class TestDecomposeInvokerFailure:
    def test_invoker_exception_wraps_as_runtime_error(self):
        invoker = MagicMock()
        invoker.invoke.side_effect = RuntimeError("Agent timeout")
        routing = RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="test")
        with pytest.raises(RuntimeError, match="CA decomposition invocation failed"):
            decompose("spec", "plan", routing, invoker)

    def test_invoker_value_error_wraps(self):
        invoker = MagicMock()
        invoker.invoke.side_effect = ValueError("Parse failure")
        routing = RoutingResult(path=RoutePath.FULL_PROJECT, reasoning="test")
        with pytest.raises(RuntimeError, match="CA decomposition invocation failed"):
            decompose("spec", "plan", routing, invoker)
