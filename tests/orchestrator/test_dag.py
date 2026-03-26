"""Tests for DAG execution engine."""

import pytest
from unittest.mock import MagicMock

from devteam.orchestrator.dag import (
    DAGExecutor,
    DAGState,
    TaskNode,
    build_dag,
)
from devteam.orchestrator.schemas import (
    DecompositionResult,
    TaskDecomposition,
)


def _make_task(id: str, depends_on: list[str] | None = None) -> TaskDecomposition:
    return TaskDecomposition(
        id=id,
        description=f"Task {id}",
        assigned_to="backend_engineer",
        team="a",
        depends_on=depends_on or [],
        pr_group="feat/main",
    )


# ---------------------------------------------------------------------------
# build_dag
# ---------------------------------------------------------------------------


class TestBuildDAG:
    def test_builds_from_decomposition(self) -> None:
        decomp = DecompositionResult(
            tasks=[
                _make_task("T-1"),
                _make_task("T-2", depends_on=["T-1"]),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        assert len(dag.nodes) == 2
        assert dag.dependency_graph["T-2"] == ["T-1"]

    def test_no_dependencies(self) -> None:
        decomp = DecompositionResult(
            tasks=[_make_task("T-1"), _make_task("T-2")],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        assert dag.dependency_graph["T-1"] == []
        assert dag.dependency_graph["T-2"] == []

    def test_cyclic_dependency_raises(self) -> None:
        """build_dag should reject graphs with cycles."""
        t1 = TaskDecomposition.model_construct(
            id="T-1",
            description="Task T-1",
            assigned_to="backend_engineer",
            team="a",
            depends_on=["T-2"],
            pr_group="feat/main",
        )
        t2 = TaskDecomposition.model_construct(
            id="T-2",
            description="Task T-2",
            assigned_to="backend_engineer",
            team="a",
            depends_on=["T-1"],
            pr_group="feat/main",
        )
        decomp = DecompositionResult.model_construct(
            tasks=[t1, t2],
            peer_assignments={},
            parallel_groups=[],
        )
        with pytest.raises(ValueError, match="Dependency cycle detected"):
            build_dag(decomp)

    def test_unknown_dependency_raises(self) -> None:
        # Use model_construct to bypass DecompositionResult's own graph
        # validation so we can test the build_dag guard in isolation.
        t1 = _make_task("T-1")
        t2_raw = TaskDecomposition.model_construct(
            id="T-2",
            description="Task T-2",
            assigned_to="backend_engineer",
            team="a",
            depends_on=["T-99"],
            pr_group="feat/main",
        )
        decomp = DecompositionResult.model_construct(
            tasks=[t1, t2_raw],
            peer_assignments={},
            parallel_groups=[],
        )
        with pytest.raises(ValueError, match="Task T-2 depends on unknown task T-99"):
            build_dag(decomp)


# ---------------------------------------------------------------------------
# DAGState
# ---------------------------------------------------------------------------


class TestDAGState:
    def test_get_ready_no_deps(self) -> None:
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        dag.nodes["T-2"] = TaskNode(task=_make_task("T-2"))
        dag.dependency_graph = {"T-1": [], "T-2": []}

        ready = dag.get_ready_tasks()
        assert len(ready) == 2

    def test_get_ready_respects_deps(self) -> None:
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        dag.nodes["T-2"] = TaskNode(task=_make_task("T-2", ["T-1"]))
        dag.dependency_graph = {"T-1": [], "T-2": ["T-1"]}

        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "T-1"

    def test_get_ready_after_dep_completes(self) -> None:
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        dag.nodes["T-2"] = TaskNode(task=_make_task("T-2", ["T-1"]))
        dag.dependency_graph = {"T-1": [], "T-2": ["T-1"]}

        dag.mark_completed("T-1", {"ok": True})
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "T-2"

    def test_running_tasks_not_ready(self) -> None:
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        dag.dependency_graph = {"T-1": []}

        dag.mark_running("T-1")
        ready = dag.get_ready_tasks()
        assert len(ready) == 0

    def test_has_pending(self) -> None:
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        assert dag.has_pending
        dag.mark_running("T-1")
        assert not dag.has_pending

    def test_all_completed(self) -> None:
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        assert not dag.all_completed
        dag.mark_completed("T-1", "done")
        assert dag.all_completed

    def test_all_completed_includes_failed(self) -> None:
        """A failed task also counts as 'complete' for DAG termination."""
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        dag.mark_failed("T-1", "error")
        assert dag.all_completed

    def test_get_results_only_completed(self) -> None:
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        dag.nodes["T-2"] = TaskNode(task=_make_task("T-2"))
        dag.dependency_graph = {"T-1": [], "T-2": []}
        dag.mark_completed("T-1", "ok")
        dag.mark_failed("T-2", "boom")

        results = dag.get_results()
        assert "T-1" in results
        assert "T-2" not in results

    def test_get_running_task_ids(self) -> None:
        dag = DAGState()
        dag.nodes["T-1"] = TaskNode(task=_make_task("T-1"))
        dag.nodes["T-2"] = TaskNode(task=_make_task("T-2"))
        dag.dependency_graph = {"T-1": [], "T-2": []}
        dag.mark_running("T-1")

        assert dag.get_running_task_ids() == ["T-1"]

    def test_empty_dag(self) -> None:
        dag = DAGState()
        assert not dag.has_pending
        assert not dag.has_running
        assert not dag.has_failed
        assert dag.all_completed
        assert dag.get_ready_tasks() == []
        assert dag.get_results() == {}


# ---------------------------------------------------------------------------
# DAGExecutor
# ---------------------------------------------------------------------------


class TestDAGExecutor:
    def test_independent_tasks_all_launched(self) -> None:
        """Two independent tasks should both be launched."""
        launched: list[str] = []

        def launch(task: TaskDecomposition) -> str:
            launched.append(task.id)
            return task.id

        results = {"T-1": "result1", "T-2": "result2"}

        def wait(handle: str) -> tuple[bool, object]:
            return (True, results[handle])

        decomp = DecompositionResult(
            tasks=[_make_task("T-1"), _make_task("T-2")],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert result.all_succeeded
        assert "T-1" in launched
        assert "T-2" in launched

    def test_dependent_tasks_run_in_order(self) -> None:
        """T-2 depends on T-1, so T-1 must complete first."""
        launch_order: list[str] = []

        def launch(task: TaskDecomposition) -> str:
            launch_order.append(task.id)
            return task.id

        def wait(handle: str) -> tuple[bool, object]:
            return (True, f"result-{handle}")

        decomp = DecompositionResult(
            tasks=[
                _make_task("T-1"),
                _make_task("T-2", depends_on=["T-1"]),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert result.all_succeeded
        assert launch_order.index("T-1") < launch_order.index("T-2")

    def test_linear_chain(self) -> None:
        """T-1 -> T-2 -> T-3 must execute in strict order."""
        launch_order: list[str] = []

        def launch(task: TaskDecomposition) -> str:
            launch_order.append(task.id)
            return task.id

        def wait(handle: str) -> tuple[bool, object]:
            return (True, f"result-{handle}")

        decomp = DecompositionResult(
            tasks=[
                _make_task("T-1"),
                _make_task("T-2", depends_on=["T-1"]),
                _make_task("T-3", depends_on=["T-2"]),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert result.all_succeeded
        assert launch_order == ["T-1", "T-2", "T-3"]

    def test_diamond_dependency(self) -> None:
        """
        T-1 -> T-2 -> T-4
        T-1 -> T-3 -> T-4
        T-2 and T-3 can run in parallel after T-1.
        """
        launch_order: list[str] = []

        def launch(task: TaskDecomposition) -> str:
            launch_order.append(task.id)
            return task.id

        def wait(handle: str) -> tuple[bool, object]:
            return (True, f"result-{handle}")

        decomp = DecompositionResult(
            tasks=[
                _make_task("T-1"),
                _make_task("T-2", depends_on=["T-1"]),
                _make_task("T-3", depends_on=["T-1"]),
                _make_task("T-4", depends_on=["T-2", "T-3"]),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert result.all_succeeded
        assert launch_order[0] == "T-1"
        assert launch_order[-1] == "T-4"
        # T-2 and T-3 should both appear before T-4
        assert launch_order.index("T-2") < launch_order.index("T-4")
        assert launch_order.index("T-3") < launch_order.index("T-4")

    def test_failed_task_reported(self) -> None:
        """Failed tasks should be captured in results."""

        def launch(task: TaskDecomposition) -> str:
            return task.id

        def wait(handle: str) -> tuple[bool, object]:
            if handle == "T-1":
                return (True, RuntimeError("Agent crashed"))
            return (True, "ok")

        decomp = DecompositionResult(
            tasks=[_make_task("T-1"), _make_task("T-2")],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert not result.all_succeeded
        assert "T-1" in result.failed_tasks
        assert "T-2" in result.results

    def test_blocked_by_failed_dependency(self) -> None:
        """If T-1 fails, T-2 (which depends on T-1) should never launch."""
        launched: list[str] = []

        def launch(task: TaskDecomposition) -> str:
            launched.append(task.id)
            return task.id

        def wait(handle: str) -> tuple[bool, object]:
            if handle == "T-1":
                return (True, RuntimeError("Failed"))
            return (True, "ok")

        decomp = DecompositionResult(
            tasks=[
                _make_task("T-1"),
                _make_task("T-2", depends_on=["T-1"]),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert "T-2" not in launched
        assert not result.all_succeeded

    def test_callbacks_invoked(self) -> None:
        on_complete = MagicMock()
        on_failed = MagicMock()

        def launch(task: TaskDecomposition) -> str:
            return task.id

        def wait(handle: str) -> tuple[bool, object]:
            if handle == "T-2":
                return (True, RuntimeError("boom"))
            return (True, "ok")

        decomp = DecompositionResult(
            tasks=[_make_task("T-1"), _make_task("T-2")],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(
            launch_task=launch,
            check_complete=wait,
            on_task_complete=on_complete,
            on_task_failed=on_failed,
        )
        executor.execute(dag)

        on_complete.assert_called_once_with("T-1", "ok")
        on_failed.assert_called_once_with("T-2", "boom")

    def test_single_task(self) -> None:
        """A single task DAG should work fine."""

        def launch(task: TaskDecomposition) -> str:
            return task.id

        def wait(handle: str) -> tuple[bool, object]:
            return (True, "done")

        decomp = DecompositionResult(
            tasks=[_make_task("T-1")],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert result.all_succeeded
        assert result.results == {"T-1": "done"}
        assert result.failed_tasks == {}

    def test_empty_dag(self) -> None:
        """An empty DAG should return immediately with success."""

        def launch(task: TaskDecomposition) -> str:
            raise AssertionError("Should not be called")

        def wait(handle: str) -> tuple[bool, object]:
            raise AssertionError("Should not be called")

        dag = DAGState()
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert result.all_succeeded
        assert result.results == {}
        assert result.failed_tasks == {}

    def test_timeout_raises(self) -> None:
        """DAG execution should raise RuntimeError when max_wait_seconds exceeded."""

        def launch(task: TaskDecomposition) -> str:
            return task.id

        def wait(handle: str) -> tuple[bool, object]:
            # Never completes
            return (False, None)

        decomp = DecompositionResult(
            tasks=[_make_task("T-1")],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(
            launch_task=launch,
            check_complete=wait,
            max_wait_seconds=0.0,
        )

        with pytest.raises(RuntimeError, match="DAG execution timed out"):
            executor.execute(dag)

    def test_launch_exception_marks_task_failed(self) -> None:
        """If launch_task raises, the task is marked failed and dependents are blocked."""
        launched: list[str] = []

        def launch(task: TaskDecomposition) -> str:
            if task.id == "T-1":
                raise RuntimeError("Agent unavailable")
            launched.append(task.id)
            return task.id

        def wait(handle: str) -> tuple[bool, object]:
            return (True, "ok")

        decomp = DecompositionResult(
            tasks=[
                _make_task("T-1"),
                _make_task("T-2", depends_on=["T-1"]),
                _make_task("T-3"),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert not result.all_succeeded
        assert "T-1" in result.failed_tasks
        assert "Agent unavailable" in result.failed_tasks["T-1"]
        # T-2 depends on T-1, so should never launch
        assert "T-2" not in launched
        # T-3 is independent, should succeed
        assert "T-3" in result.results

    def test_check_complete_exception_marks_task_failed(self) -> None:
        """If check_complete raises, the task is marked failed without aborting the DAG."""

        def launch(task: TaskDecomposition) -> str:
            return task.id

        def wait(handle: str) -> tuple[bool, object]:
            if handle == "T-1":
                raise ConnectionError("transport error")
            return (True, "ok")

        decomp = DecompositionResult(
            tasks=[_make_task("T-1"), _make_task("T-2")],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert not result.all_succeeded
        assert "T-1" in result.failed_tasks
        assert "transport error" in result.failed_tasks["T-1"]
        # T-2 is independent and should still succeed
        assert "T-2" in result.results

    def test_blocked_tasks_tracked_when_dependency_fails(self) -> None:
        """When T-1 fails, T-2 (depends on T-1) should appear in blocked_tasks."""

        def launch(task: TaskDecomposition) -> str:
            return task.id

        def wait(handle: str) -> tuple[bool, object]:
            if handle == "T-1":
                return (True, RuntimeError("Failed"))
            return (True, "ok")

        decomp = DecompositionResult(
            tasks=[
                _make_task("T-1"),
                _make_task("T-2", depends_on=["T-1"]),
                _make_task("T-3"),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert not result.all_succeeded
        assert "T-1" in result.failed_tasks
        assert "T-2" in result.blocked_tasks
        assert "T-3" not in result.blocked_tasks
        assert "T-3" in result.results

    def test_empty_dag_no_blocked_tasks(self) -> None:
        """An empty DAG should have no blocked tasks."""

        def launch(task: TaskDecomposition) -> str:
            raise AssertionError("Should not be called")

        def wait(handle: str) -> tuple[bool, object]:
            raise AssertionError("Should not be called")

        dag = DAGState()
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert result.blocked_tasks == []

    def test_blocked_tasks_in_diamond_dependency(self) -> None:
        """In a diamond, if T-1 fails, T-2, T-3, and T-4 are all blocked."""

        def launch(task: TaskDecomposition) -> str:
            return task.id

        def wait(handle: str) -> tuple[bool, object]:
            if handle == "T-1":
                return (True, RuntimeError("Failed"))
            return (True, "ok")

        decomp = DecompositionResult(
            tasks=[
                _make_task("T-1"),
                _make_task("T-2", depends_on=["T-1"]),
                _make_task("T-3", depends_on=["T-1"]),
                _make_task("T-4", depends_on=["T-2", "T-3"]),
            ],
            peer_assignments={},
            parallel_groups=[],
        )
        dag = build_dag(decomp)
        executor = DAGExecutor(launch_task=launch, check_complete=wait)
        result = executor.execute(dag)

        assert "T-1" in result.failed_tasks
        assert sorted(result.blocked_tasks) == ["T-2", "T-3", "T-4"]
