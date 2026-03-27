"""DAG execution engine -- dependency-aware parallel task dispatch.

Manages a directed acyclic graph of tasks, launching tasks whose
dependencies are satisfied and waiting for completion events.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from devteam.orchestrator.schemas import (
    DecompositionResult,
    TaskDecomposition,
)


class TaskState(str, Enum):
    """Execution state of a single task within the DAG."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskNode:
    """A node in the task DAG."""

    task: TaskDecomposition
    state: TaskState = TaskState.PENDING
    result: Any = None
    error: str | None = None


@dataclass
class DAGState:
    """Current state of the DAG execution."""

    nodes: dict[str, TaskNode] = field(default_factory=dict)
    dependency_graph: dict[str, list[str]] = field(default_factory=dict)

    @property
    def has_pending(self) -> bool:
        return any(n.state == TaskState.PENDING for n in self.nodes.values())

    @property
    def has_running(self) -> bool:
        return any(n.state == TaskState.RUNNING for n in self.nodes.values())

    @property
    def has_failed(self) -> bool:
        return any(n.state == TaskState.FAILED for n in self.nodes.values())

    @property
    def all_completed(self) -> bool:
        return all(n.state in (TaskState.COMPLETED, TaskState.FAILED) for n in self.nodes.values())

    def get_ready_tasks(self) -> list[TaskDecomposition]:
        """Return tasks whose dependencies are all completed."""
        ready = []
        for tid, node in self.nodes.items():
            if node.state != TaskState.PENDING:
                continue
            deps = self.dependency_graph.get(tid, [])
            if all(self.nodes[d].state == TaskState.COMPLETED for d in deps if d in self.nodes):
                ready.append(node.task)
        return ready

    def get_running_task_ids(self) -> list[str]:
        return [tid for tid, node in self.nodes.items() if node.state == TaskState.RUNNING]

    def mark_running(self, task_id: str) -> None:
        self.nodes[task_id].state = TaskState.RUNNING

    def mark_completed(self, task_id: str, result: Any) -> None:
        self.nodes[task_id].state = TaskState.COMPLETED
        self.nodes[task_id].result = result

    def mark_failed(self, task_id: str, error: str) -> None:
        self.nodes[task_id].state = TaskState.FAILED
        self.nodes[task_id].error = error

    def get_results(self) -> dict[str, Any]:
        return {
            tid: node.result
            for tid, node in self.nodes.items()
            if node.state == TaskState.COMPLETED
        }


def build_dag(decomposition: DecompositionResult) -> DAGState:
    """Build a DAG from decomposition result."""
    dag = DAGState()
    for task in decomposition.tasks:
        dag.nodes[task.id] = TaskNode(task=task)
        dag.dependency_graph[task.id] = list(task.depends_on)

    all_task_ids = {t.id for t in decomposition.tasks}
    for tid, deps in dag.dependency_graph.items():
        for dep in deps:
            if dep not in all_task_ids:
                raise ValueError(f"Task {tid} depends on unknown task {dep}")

    # Cycle detection via DFS
    visited: set[str] = set()
    in_stack: set[str] = set()

    def _dfs(node: str) -> None:
        if node in in_stack:
            raise ValueError(f"Dependency cycle detected involving task {node}")
        if node in visited:
            return
        in_stack.add(node)
        for dep in dag.dependency_graph.get(node, []):
            _dfs(dep)
        in_stack.remove(node)
        visited.add(node)

    for tid in dag.dependency_graph:
        _dfs(tid)

    return dag


@dataclass
class DAGExecutionResult:
    """Result of executing the full DAG."""

    results: dict[str, Any]
    failed_tasks: dict[str, str]  # task_id -> error message
    all_succeeded: bool
    blocked_tasks: list[str] = field(default_factory=list)


class DAGExecutor:
    """Executes a task DAG with dependency-aware parallel dispatch.

    The executor is designed to be used with DBOS workflows. In production,
    ``launch_task`` starts a child workflow and ``check_complete`` polls
    for completion (non-blocking). In tests, ``check_complete`` returns
    immediately with (True, result).
    """

    def __init__(
        self,
        launch_task: Callable[[TaskDecomposition], str],
        check_complete: Callable[[str], tuple[bool, Any]],
        on_task_complete: Callable[[str, Any], None] | None = None,
        on_task_failed: Callable[[str, str], None] | None = None,
        max_wait_seconds: float = 3600.0,
    ) -> None:
        """Initialise the executor.

        Args:
            launch_task: Starts a task workflow, returns a handle/id.
            check_complete: Non-blocking check. Returns (done, result_or_error).
                If done is False, result_or_error is None.
                If done is True and the task failed, result_or_error
                is an Exception. Otherwise it is the task result.
            on_task_complete: Optional callback when a task completes.
            on_task_failed: Optional callback when a task fails.
            max_wait_seconds: Maximum wall-clock time for the entire DAG
                execution before raising RuntimeError.
        """
        self._launch = launch_task
        self._check_complete = check_complete
        self._on_complete = on_task_complete
        self._on_failed = on_task_failed
        self._max_wait_seconds = max_wait_seconds

    def execute(self, dag: DAGState) -> DAGExecutionResult:
        """Execute the DAG, respecting dependencies.

        Algorithm:
        1. Find all tasks with satisfied dependencies
        2. Launch them in parallel
        3. Wait for any one to complete
        4. Mark it completed, loop back to find newly unblocked tasks
        5. Repeat until all tasks are done or failed
        """
        handles: dict[str, str] = {}  # task_id -> handle
        start_time = time.monotonic()

        while dag.has_pending or dag.has_running:
            # Launch ready tasks
            for task in dag.get_ready_tasks():
                if task.id not in handles:
                    try:
                        handle = self._launch(task)
                    except Exception as exc:
                        dag.mark_failed(task.id, str(exc))
                        if self._on_failed:
                            self._on_failed(task.id, str(exc))
                        continue
                    handles[task.id] = handle
                    dag.mark_running(task.id)

            # If nothing is running and nothing is ready, we are stuck
            if not dag.has_running:
                break

            # Wait for ANY running task to complete.
            completed_tid: str | None = None
            while completed_tid is None:
                for tid in list(dag.get_running_task_ids()):
                    if tid not in handles:
                        continue
                    try:
                        done, result_or_error = self._check_complete(handles[tid])
                    except Exception as exc:
                        dag.mark_failed(tid, str(exc))
                        if self._on_failed:
                            self._on_failed(tid, str(exc))
                        handles.pop(tid, None)
                        completed_tid = tid
                        break
                    if not done:
                        continue
                    if isinstance(result_or_error, Exception):
                        dag.mark_failed(tid, str(result_or_error))
                        if self._on_failed:
                            self._on_failed(tid, str(result_or_error))
                    else:
                        dag.mark_completed(tid, result_or_error)
                        if self._on_complete:
                            self._on_complete(tid, result_or_error)
                    handles.pop(tid, None)
                    completed_tid = tid
                    break  # Process one completion, then re-check ready tasks
                if completed_tid is None:
                    if time.monotonic() - start_time > self._max_wait_seconds:
                        raise RuntimeError("DAG execution timed out")
                    time.sleep(0.1)  # Brief poll interval; no-op in sync tests

        # Tasks still PENDING after execution loop are blocked (their
        # dependencies failed, so they could never become ready).
        blocked = [tid for tid, node in dag.nodes.items() if node.state == TaskState.PENDING]

        return DAGExecutionResult(
            results=dag.get_results(),
            failed_tasks={
                tid: node.error or "Unknown error"
                for tid, node in dag.nodes.items()
                if node.state == TaskState.FAILED
            },
            all_succeeded=not dag.has_failed
            and all(n.state == TaskState.COMPLETED for n in dag.nodes.values()),
            blocked_tasks=blocked,
        )
