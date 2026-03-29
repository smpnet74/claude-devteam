"""Extended tests for git step wrappers in runtime.py.

Complements test_runtime.py with thorough coverage of argument forwarding,
error propagation, and edge cases for create_worktree_step, create_pr_step,
and cleanup_step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from dbos import DBOS

from devteam.git.cleanup import CleanupResult
from devteam.git.pr import PRInfo
from devteam.git.worktree import WorktreeInfo


# ---------------------------------------------------------------------------
# TestCreateWorktreeStep — extended
# ---------------------------------------------------------------------------


class TestCreateWorktreeStepExtended:
    @pytest.mark.asyncio
    async def test_base_ref_forwarded(self, dbos_launch: Any, tmp_path: Path) -> None:
        """base_ref parameter is passed through to create_worktree."""
        from devteam.orchestrator.runtime import create_worktree_step

        expected = WorktreeInfo(
            path=tmp_path / ".worktrees" / "feat", branch="feat/x", is_main=False
        )

        with patch("devteam.orchestrator.runtime.create_worktree", return_value=expected) as mock:

            @DBOS.workflow()
            async def _run() -> WorktreeInfo:
                return await create_worktree_step(
                    repo_root=tmp_path, branch="feat/x", base_ref="v1.0"
                )

            result = await _run()
            assert result.branch == "feat/x"
            mock.assert_called_once_with(tmp_path, "feat/x", base_ref="v1.0")

    @pytest.mark.asyncio
    async def test_base_ref_none_by_default(self, dbos_launch: Any, tmp_path: Path) -> None:
        """base_ref defaults to None."""
        from devteam.orchestrator.runtime import create_worktree_step

        expected = WorktreeInfo(
            path=tmp_path / ".worktrees" / "feat", branch="feat/x", is_main=False
        )

        with patch("devteam.orchestrator.runtime.create_worktree", return_value=expected) as mock:

            @DBOS.workflow()
            async def _run() -> WorktreeInfo:
                return await create_worktree_step(repo_root=tmp_path, branch="feat/x")

            await _run()
            mock.assert_called_once_with(tmp_path, "feat/x", base_ref=None)

    @pytest.mark.asyncio
    async def test_error_propagates(self, dbos_launch: Any, tmp_path: Path) -> None:
        """Errors from create_worktree propagate through the step."""
        from devteam.orchestrator.runtime import create_worktree_step

        with patch(
            "devteam.orchestrator.runtime.create_worktree",
            side_effect=ValueError("branch already exists"),
        ):

            @DBOS.workflow()
            async def _run() -> WorktreeInfo:
                return await create_worktree_step(repo_root=tmp_path, branch="feat/dup")

            with pytest.raises(ValueError, match="branch already exists"):
                await _run()


# ---------------------------------------------------------------------------
# TestCreatePRStep — extended
# ---------------------------------------------------------------------------


class TestCreatePRStepExtended:
    @pytest.mark.asyncio
    async def test_upstream_repo_forwarded(self, dbos_launch: Any, tmp_path: Path) -> None:
        """upstream_repo is forwarded for fork workflows."""
        from devteam.orchestrator.runtime import create_pr_step

        expected = PRInfo(
            number=99, url="https://github.com/upstream/repo/pull/99", branch="feat/x", title="X"
        )

        with patch("devteam.orchestrator.runtime.create_pr", return_value=expected) as mock:

            @DBOS.workflow()
            async def _run() -> PRInfo:
                return await create_pr_step(
                    cwd=tmp_path,
                    title="Fix X",
                    body="Fixes issue",
                    branch="feat/x",
                    base="develop",
                    upstream_repo="upstream/repo",
                )

            result = await _run()
            assert result.number == 99
            mock.assert_called_once_with(
                cwd=tmp_path,
                title="Fix X",
                body="Fixes issue",
                branch="feat/x",
                base="develop",
                upstream_repo="upstream/repo",
            )

    @pytest.mark.asyncio
    async def test_default_base_is_main(self, dbos_launch: Any, tmp_path: Path) -> None:
        """Default base branch is 'main'."""
        from devteam.orchestrator.runtime import create_pr_step

        expected = PRInfo(number=1, url="https://github.com/o/r/pull/1", branch="feat/y", title="Y")

        with patch("devteam.orchestrator.runtime.create_pr", return_value=expected) as mock:

            @DBOS.workflow()
            async def _run() -> PRInfo:
                return await create_pr_step(cwd=tmp_path, title="Y", body="body", branch="feat/y")

            await _run()
            _, kwargs = mock.call_args
            assert kwargs["base"] == "main"
            assert kwargs["upstream_repo"] is None

    @pytest.mark.asyncio
    async def test_error_propagates(self, dbos_launch: Any, tmp_path: Path) -> None:
        """Errors from create_pr propagate through the step."""
        from devteam.orchestrator.runtime import create_pr_step

        with patch(
            "devteam.orchestrator.runtime.create_pr",
            side_effect=RuntimeError("gh not found"),
        ):

            @DBOS.workflow()
            async def _run() -> PRInfo:
                return await create_pr_step(cwd=tmp_path, title="X", body="body", branch="feat/x")

            with pytest.raises(RuntimeError, match="gh not found"):
                await _run()


# ---------------------------------------------------------------------------
# TestCleanupStep — extended
# ---------------------------------------------------------------------------


class TestCleanupStepExtended:
    @pytest.mark.asyncio
    async def test_merge_with_worktree_path(self, dbos_launch: Any, tmp_path: Path) -> None:
        """Merge mode forwards worktree_path."""
        from devteam.orchestrator.runtime import cleanup_step

        wt = tmp_path / "worktrees" / "feat"
        expected = CleanupResult(success=True)

        with patch(
            "devteam.orchestrator.runtime.cleanup_after_merge", return_value=expected
        ) as mock:

            @DBOS.workflow()
            async def _run() -> CleanupResult:
                return await cleanup_step(
                    repo_root=tmp_path, branch="feat/x", mode="merge", worktree_path=wt
                )

            result = await _run()
            assert result.success
            mock.assert_called_once_with(repo_root=tmp_path, branch="feat/x", worktree_path=wt)

    @pytest.mark.asyncio
    async def test_cancel_custom_comment(self, dbos_launch: Any, tmp_path: Path) -> None:
        """Cancel mode forwards custom comment."""
        from devteam.orchestrator.runtime import cleanup_step

        expected = CleanupResult(success=True)

        with patch("devteam.orchestrator.runtime.cleanup_single_pr", return_value=expected) as mock:

            @DBOS.workflow()
            async def _run() -> CleanupResult:
                return await cleanup_step(
                    repo_root=tmp_path,
                    branch="feat/x",
                    mode="cancel",
                    pr_number=55,
                    comment="Superseded by PR #60",
                )

            result = await _run()
            assert result.success
            mock.assert_called_once_with(
                repo_root=tmp_path,
                branch="feat/x",
                pr_number=55,
                worktree_path=None,
                comment="Superseded by PR #60",
            )

    @pytest.mark.asyncio
    async def test_cancel_with_worktree(self, dbos_launch: Any, tmp_path: Path) -> None:
        """Cancel mode forwards worktree_path."""
        from devteam.orchestrator.runtime import cleanup_step

        wt = tmp_path / "worktrees" / "feat"
        expected = CleanupResult(success=True)

        with patch("devteam.orchestrator.runtime.cleanup_single_pr", return_value=expected) as mock:

            @DBOS.workflow()
            async def _run() -> CleanupResult:
                return await cleanup_step(
                    repo_root=tmp_path,
                    branch="feat/x",
                    mode="cancel",
                    pr_number=42,
                    worktree_path=wt,
                )

            await _run()
            _, kwargs = mock.call_args
            assert kwargs["worktree_path"] == wt

    @pytest.mark.asyncio
    async def test_merge_error_propagates(self, dbos_launch: Any, tmp_path: Path) -> None:
        """Errors from cleanup_after_merge propagate."""
        from devteam.orchestrator.runtime import cleanup_step

        with patch(
            "devteam.orchestrator.runtime.cleanup_after_merge",
            side_effect=OSError("permission denied"),
        ):

            @DBOS.workflow()
            async def _run() -> CleanupResult:
                return await cleanup_step(repo_root=tmp_path, branch="feat/x", mode="merge")

            with pytest.raises(OSError, match="permission denied"):
                await _run()
