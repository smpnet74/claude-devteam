"""Tests for DBOS step wrappers in orchestrator.runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dbos import DBOS

from devteam.orchestrator.schemas import (
    DecompositionResult,
    RoutePath,
    RoutingResult,
    WorkType,
)
from devteam.orchestrator.routing import IntakeContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_routing_result(
    path: RoutePath = RoutePath.FULL_PROJECT,
    reasoning: str = "test routing",
    target_team: str | None = None,
) -> RoutingResult:
    return RoutingResult(path=path, reasoning=reasoning, target_team=target_team)


def _make_decomposition_dict() -> dict[str, Any]:
    """Return a raw dict that can be validated into a DecompositionResult."""
    return {
        "tasks": [
            {
                "id": "T-1",
                "description": "Build login API",
                "assigned_to": "backend_engineer",
                "team": "a",
                "depends_on": [],
                "pr_group": "auth",
                "work_type": "code",
            },
            {
                "id": "T-2",
                "description": "Build login UI",
                "assigned_to": "frontend_engineer",
                "team": "a",
                "depends_on": ["T-1"],
                "pr_group": "auth",
                "work_type": "code",
            },
        ],
        "peer_assignments": {"T-1": "frontend_engineer", "T-2": "backend_engineer"},
        "parallel_groups": [["T-1"]],
    }


def _make_review_result_dict(verdict: str = "approved") -> dict[str, Any]:
    d: dict[str, Any] = {"verdict": verdict, "summary": "Looks good"}
    if verdict in ("needs_revision", "approved_with_comments", "blocked"):
        d["comments"] = [
            {"file": "main.py", "line": 1, "severity": "warning", "comment": "fix this"}
        ]
    return d


# ---------------------------------------------------------------------------
# TestRouteIntakeStep
# ---------------------------------------------------------------------------


class TestRouteIntakeStep:
    """Tests for route_intake_step."""

    @pytest.mark.asyncio
    async def test_fast_path_spec_and_plan(self, dbos_launch: Any) -> None:
        """When spec+plan provided, classify_intake fast-paths to FULL_PROJECT."""
        from devteam.orchestrator.runtime import route_intake_step

        ctx = IntakeContext(spec="Build auth", plan="Step 1: API")

        @DBOS.workflow()
        async def _run() -> RoutingResult:
            return await route_intake_step(ctx, project_name="myproj", worktree_path="/tmp")

        result = await _run()
        assert result.path == RoutePath.FULL_PROJECT
        assert "direct" in result.reasoning.lower() or "spec" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_ceo_routing_via_invoke(self, dbos_launch: Any) -> None:
        """When no fast-path, CEO agent is invoked for routing."""
        from devteam.orchestrator.runtime import route_intake_step

        ctx = IntakeContext(prompt="Research best CI practices")

        ceo_result = RoutingResult(
            path=RoutePath.RESEARCH,
            reasoning="This is a research request",
        )

        with patch(
            "devteam.orchestrator.runtime.invoke_agent_step",
            new_callable=AsyncMock,
            return_value=ceo_result.model_dump(),
        ):

            @DBOS.workflow()
            async def _run() -> RoutingResult:
                return await route_intake_step(
                    ctx, project_name="myproj", worktree_path="/tmp/repo"
                )

            result = await _run()
            assert result.path == RoutePath.RESEARCH


# ---------------------------------------------------------------------------
# TestDecomposeStep
# ---------------------------------------------------------------------------


class TestDecomposeStep:
    """Tests for decompose_step."""

    @pytest.mark.asyncio
    async def test_decompose_returns_valid_result(self, dbos_launch: Any) -> None:
        """decompose_step invokes CA and returns validated DecompositionResult."""
        from devteam.orchestrator.runtime import decompose_step

        routing = _make_routing_result()
        raw_decomp = _make_decomposition_dict()

        with patch(
            "devteam.orchestrator.runtime.invoke_agent_step",
            new_callable=AsyncMock,
            return_value=raw_decomp,
        ):

            @DBOS.workflow()
            async def _run() -> DecompositionResult:
                return await decompose_step(
                    spec="Build auth system",
                    plan="Step 1: API\nStep 2: UI",
                    routing=routing,
                    project_name="myproj",
                    worktree_path="/tmp/repo",
                )

            result = await _run()
            assert len(result.tasks) == 2
            assert result.tasks[0].id == "T-1"
            # Peer assignments should be filled in
            assert "T-1" in result.peer_assignments
            assert "T-2" in result.peer_assignments

    @pytest.mark.asyncio
    async def test_decompose_rejects_non_full_project(self, dbos_launch: Any) -> None:
        """decompose_step raises ValueError for non-FULL_PROJECT routes."""
        from devteam.orchestrator.runtime import decompose_step

        routing = _make_routing_result(
            path=RoutePath.SMALL_FIX,
            reasoning="small fix",
            target_team="a",
        )

        @DBOS.workflow()
        async def _run() -> DecompositionResult:
            return await decompose_step(
                spec="Fix typo",
                plan="Edit readme",
                routing=routing,
                project_name="myproj",
                worktree_path="/tmp/repo",
            )

        with pytest.raises(ValueError, match="FULL_PROJECT"):
            await _run()


# ---------------------------------------------------------------------------
# TestPostPRReviewStep
# ---------------------------------------------------------------------------


class TestPostPRReviewStep:
    """Tests for post_pr_review_step."""

    @pytest.mark.asyncio
    async def test_review_all_pass(self, dbos_launch: Any) -> None:
        """All review gates pass for CODE work type."""
        from devteam.orchestrator.runtime import post_pr_review_step

        review_dict = _make_review_result_dict("approved")

        with patch(
            "devteam.orchestrator.runtime.invoke_agent_step",
            new_callable=AsyncMock,
            return_value=review_dict,
        ):

            @DBOS.workflow()
            async def _run():
                return await post_pr_review_step(
                    work_type=WorkType.CODE,
                    pr_context="PR diff content here",
                    project_name="test",
                    worktree_path="/tmp",
                )

            result = await _run()
            assert result.all_passed is True

    @pytest.mark.asyncio
    async def test_review_required_gate_failure_short_circuits(self, dbos_launch: Any) -> None:
        """When a required gate fails, the chain stops and all_passed is False."""
        from devteam.orchestrator.runtime import post_pr_review_step

        review_dict = _make_review_result_dict("needs_revision")

        call_count = 0
        original_mock = AsyncMock(return_value=review_dict)

        async def counting_mock(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            return await original_mock(*args, **kwargs)

        with patch(
            "devteam.orchestrator.runtime.invoke_agent_step",
            side_effect=counting_mock,
        ):

            @DBOS.workflow()
            async def _run():
                return await post_pr_review_step(
                    work_type=WorkType.CODE,
                    pr_context="PR diff content",
                    project_name="test",
                    worktree_path="/tmp",
                )

            result = await _run()
            assert result.all_passed is False
            assert len(result.failed_gates) >= 1
            # Should stop after first required gate fails, not run all gates
            assert call_count == 1

    @pytest.mark.asyncio
    async def test_review_skips_qa_for_docs_only(self, dbos_launch: Any) -> None:
        """QA gate is skipped when only doc files changed."""
        from devteam.orchestrator.runtime import post_pr_review_step

        review_dict = _make_review_result_dict("approved")

        with patch(
            "devteam.orchestrator.runtime.invoke_agent_step",
            new_callable=AsyncMock,
            return_value=review_dict,
        ):

            @DBOS.workflow()
            async def _run():
                return await post_pr_review_step(
                    work_type=WorkType.CODE,
                    pr_context="PR diff",
                    project_name="test",
                    worktree_path="/tmp",
                    files_changed=["README.md", "docs/guide.txt"],
                )

            result = await _run()
            assert result.all_passed is True
            assert "qa_review" in result.skipped_gates


# ---------------------------------------------------------------------------
# TestInvokeAgentStep
# ---------------------------------------------------------------------------


class TestInvokeAgentStep:
    """Tests for invoke_agent_step."""

    @pytest.mark.asyncio
    async def test_invoke_returns_parsed_dict(self, dbos_launch: Any) -> None:
        """invoke_agent_step calls AgentInvoker.invoke and returns dict."""
        from devteam.orchestrator.runtime import (
            invoke_agent_step,
            set_invoker,
        )

        mock_invoker = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {"path": "research", "reasoning": "test"}
        mock_invoker.invoke = AsyncMock(return_value=mock_result)

        set_invoker(mock_invoker)
        try:

            @DBOS.workflow()
            async def _run() -> dict[str, Any]:
                return await invoke_agent_step(
                    role="ceo",
                    prompt="test prompt",
                    worktree_path="/tmp/test",
                    project_name="test-project",
                )

            result = await _run()
            assert result == {"path": "research", "reasoning": "test"}
            mock_invoker.invoke.assert_awaited_once()
        finally:
            set_invoker(None)

    @pytest.mark.asyncio
    async def test_invoke_wraps_invoker_errors(self, dbos_launch: Any) -> None:
        """invoke_agent_step wraps exceptions with role/project context."""
        from devteam.orchestrator.runtime import invoke_agent_step, set_invoker

        mock_invoker = MagicMock()
        mock_invoker.invoke = AsyncMock(side_effect=ConnectionError("LLM timeout"))

        set_invoker(mock_invoker)
        try:

            @DBOS.workflow()
            async def _run() -> dict[str, Any]:
                return await invoke_agent_step(
                    role="backend_engineer",
                    prompt="test",
                    worktree_path="/tmp/repo",
                    project_name="myproj",
                )

            with pytest.raises(RuntimeError, match="backend_engineer") as exc_info:
                await _run()
            assert "myproj" in str(exc_info.value)
            assert "LLM timeout" in str(exc_info.value)
        finally:
            set_invoker(None)

    @pytest.mark.asyncio
    async def test_invoke_augments_prompt_with_knowledge(self, dbos_launch: Any) -> None:
        """invoke_agent_step prepends knowledge index to prompt when available."""
        from devteam.orchestrator.runtime import invoke_agent_step, set_invoker

        mock_invoker = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {"answer": "ok"}
        mock_invoker.invoke = AsyncMock(return_value=mock_result)

        set_invoker(mock_invoker)
        try:
            with patch(
                "devteam.orchestrator.runtime.build_memory_index_safe",
                new_callable=AsyncMock,
                return_value="## Available Knowledge\n- Auth patterns: JWT preferred",
            ):

                @DBOS.workflow()
                async def _run() -> dict[str, Any]:
                    return await invoke_agent_step(
                        role="ceo",
                        prompt="original prompt",
                        worktree_path="/tmp",
                        project_name="test",
                    )

                await _run()
                call_args = mock_invoker.invoke.call_args
                task_prompt = call_args.kwargs["task_prompt"]
                assert "Available Knowledge" in task_prompt
                assert "original prompt" in task_prompt
        finally:
            set_invoker(None)

    @pytest.mark.asyncio
    async def test_invoke_without_invoker_raises(self, dbos_launch: Any) -> None:
        """invoke_agent_step raises RuntimeError when no invoker is configured."""
        from devteam.orchestrator.runtime import invoke_agent_step, set_invoker

        set_invoker(None)

        @DBOS.workflow()
        async def _run() -> dict[str, Any]:
            return await invoke_agent_step(
                role="ceo",
                prompt="test",
                worktree_path="/tmp/test",
                project_name="test",
            )

        with pytest.raises(RuntimeError, match="invoker"):
            await _run()


# ---------------------------------------------------------------------------
# TestCreateWorktreeStep
# ---------------------------------------------------------------------------


class TestCreateWorktreeStep:
    """Tests for create_worktree_step."""

    @pytest.mark.asyncio
    async def test_create_worktree(self, dbos_launch: Any, tmp_path: Path) -> None:
        """create_worktree_step calls git worktree creation."""
        from devteam.git.worktree import WorktreeInfo
        from devteam.orchestrator.runtime import create_worktree_step

        expected = WorktreeInfo(
            path=tmp_path / ".worktrees" / "feat-login",
            branch="feat/login",
            commit="abc123",
        )

        with patch(
            "devteam.orchestrator.runtime.create_worktree",
            return_value=expected,
        ) as mock_create:

            @DBOS.workflow()
            async def _run() -> WorktreeInfo:
                return await create_worktree_step(
                    repo_root=tmp_path,
                    branch="feat/login",
                )

            result = await _run()
            assert result.branch == "feat/login"
            mock_create.assert_called_once_with(tmp_path, "feat/login", base_ref=None)


# ---------------------------------------------------------------------------
# TestCreatePRStep
# ---------------------------------------------------------------------------


class TestCreatePRStep:
    """Tests for create_pr_step."""

    @pytest.mark.asyncio
    async def test_create_pr(self, dbos_launch: Any, tmp_path: Path) -> None:
        """create_pr_step calls git PR creation."""
        from devteam.git.pr import PRInfo
        from devteam.orchestrator.runtime import create_pr_step

        expected = PRInfo(
            number=42,
            url="https://github.com/owner/repo/pull/42",
            branch="feat/login",
            title="Add login",
        )

        with patch(
            "devteam.orchestrator.runtime.create_pr",
            return_value=expected,
        ) as mock_pr:

            @DBOS.workflow()
            async def _run() -> PRInfo:
                return await create_pr_step(
                    cwd=tmp_path,
                    title="Add login",
                    body="Implements login flow",
                    branch="feat/login",
                )

            result = await _run()
            assert result.number == 42
            mock_pr.assert_called_once_with(
                cwd=tmp_path,
                title="Add login",
                body="Implements login flow",
                branch="feat/login",
                base="main",
                upstream_repo=None,
            )


# ---------------------------------------------------------------------------
# TestCleanupStep
# ---------------------------------------------------------------------------


class TestCleanupStep:
    """Tests for cleanup_step."""

    @pytest.mark.asyncio
    async def test_cleanup_after_merge(self, dbos_launch: Any, tmp_path: Path) -> None:
        """cleanup_step calls cleanup_after_merge."""
        from devteam.git.cleanup import CleanupResult
        from devteam.orchestrator.runtime import cleanup_step

        expected = CleanupResult(success=True)

        with patch(
            "devteam.orchestrator.runtime.cleanup_after_merge",
            return_value=expected,
        ) as mock_cleanup:

            @DBOS.workflow()
            async def _run() -> CleanupResult:
                return await cleanup_step(
                    repo_root=tmp_path,
                    branch="feat/login",
                    mode="merge",
                )

            result = await _run()
            assert result.success is True
            mock_cleanup.assert_called_once_with(
                repo_root=tmp_path,
                branch="feat/login",
                worktree_path=None,
            )

    @pytest.mark.asyncio
    async def test_cleanup_cancel_without_pr_number_raises(
        self, dbos_launch: Any, tmp_path: Path
    ) -> None:
        """cleanup_step in cancel mode raises ValueError without pr_number."""
        from devteam.orchestrator.runtime import cleanup_step

        @DBOS.workflow()
        async def _run() -> Any:
            return await cleanup_step(
                repo_root=tmp_path,
                branch="feat/login",
                mode="cancel",
            )

        with pytest.raises(ValueError, match="pr_number"):
            await _run()

    @pytest.mark.asyncio
    async def test_cleanup_cancel(self, dbos_launch: Any, tmp_path: Path) -> None:
        """cleanup_step calls cleanup_single_pr in cancel mode."""
        from devteam.git.cleanup import CleanupResult
        from devteam.orchestrator.runtime import cleanup_step

        expected = CleanupResult(success=True)

        with patch(
            "devteam.orchestrator.runtime.cleanup_single_pr",
            return_value=expected,
        ) as mock_cleanup:

            @DBOS.workflow()
            async def _run() -> CleanupResult:
                return await cleanup_step(
                    repo_root=tmp_path,
                    branch="feat/login",
                    mode="cancel",
                    pr_number=42,
                )

            result = await _run()
            assert result.success is True
            mock_cleanup.assert_called_once_with(
                repo_root=tmp_path,
                branch="feat/login",
                pr_number=42,
                worktree_path=None,
                comment="Cancelled by operator",
            )

    @pytest.mark.asyncio
    async def test_cleanup_unknown_mode_raises(self, dbos_launch: Any, tmp_path: Path) -> None:
        """cleanup_step raises ValueError for unknown mode."""
        from devteam.orchestrator.runtime import cleanup_step

        @DBOS.workflow()
        async def _run() -> Any:
            return await cleanup_step(
                repo_root=tmp_path,
                branch="feat/login",
                mode="invalid",
            )

        with pytest.raises(ValueError, match="Unknown cleanup mode"):
            await _run()


# ---------------------------------------------------------------------------
# TestMalformedPayloads
# ---------------------------------------------------------------------------


class TestMalformedPayloads:
    """Tests that model_validate boundaries reject bad agent payloads."""

    @pytest.mark.asyncio
    async def test_route_intake_rejects_malformed_agent_response(self, dbos_launch: Any) -> None:
        """route_intake_step raises when CEO returns invalid payload."""
        from devteam.orchestrator.runtime import route_intake_step

        ctx = IntakeContext(prompt="Research something")

        with patch(
            "devteam.orchestrator.runtime.invoke_agent_step",
            new_callable=AsyncMock,
            return_value={"invalid_field": "bad"},
        ):

            @DBOS.workflow()
            async def _run() -> RoutingResult:
                return await route_intake_step(ctx, project_name="test", worktree_path="/tmp")

            with pytest.raises(Exception):
                await _run()

    @pytest.mark.asyncio
    async def test_decompose_rejects_malformed_agent_response(self, dbos_launch: Any) -> None:
        """decompose_step raises when CA returns invalid payload."""
        from devteam.orchestrator.runtime import decompose_step

        routing = _make_routing_result()

        with patch(
            "devteam.orchestrator.runtime.invoke_agent_step",
            new_callable=AsyncMock,
            return_value={"not": "a valid decomposition"},
        ):

            @DBOS.workflow()
            async def _run() -> DecompositionResult:
                return await decompose_step(
                    spec="Build it",
                    plan="Steps",
                    routing=routing,
                    project_name="test",
                    worktree_path="/tmp",
                )

            with pytest.raises(Exception):
                await _run()
