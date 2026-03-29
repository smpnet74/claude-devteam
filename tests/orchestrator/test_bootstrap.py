"""Tests for bootstrap sequence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devteam.orchestrator.runtime_state import RuntimeStateStore


@pytest.fixture
def runtime_store(tmp_path: Path) -> RuntimeStateStore:
    s = RuntimeStateStore(str(tmp_path / "runtime.sqlite"))
    yield s
    s.close()


class TestGetRuntimeStore:
    def test_raises_when_not_initialized(self) -> None:
        from devteam.orchestrator import bootstrap

        original = bootstrap._runtime_store
        bootstrap._runtime_store = None
        try:
            with pytest.raises(RuntimeError, match="not initialized"):
                bootstrap.get_runtime_store()
        finally:
            bootstrap._runtime_store = original

    def test_returns_store_when_set(self, runtime_store: RuntimeStateStore) -> None:
        from devteam.orchestrator import bootstrap

        original = bootstrap._runtime_store
        bootstrap._runtime_store = runtime_store
        try:
            assert bootstrap.get_runtime_store() is runtime_store
        finally:
            bootstrap._runtime_store = original


class TestLoadAndMergeConfig:
    def test_returns_default_config_when_no_files(self, tmp_path: Path) -> None:
        from devteam.orchestrator.bootstrap import load_and_merge_config

        config = load_and_merge_config(
            global_path=tmp_path / "nonexistent" / "config.toml",
            project_path=tmp_path / "nonexistent" / "devteam.toml",
        )
        assert config.general.max_concurrent_agents == 3
        assert config.models.engineering == "sonnet"

    def test_merges_project_config(self, tmp_path: Path) -> None:
        from devteam.orchestrator.bootstrap import load_and_merge_config

        global_toml = tmp_path / "config.toml"
        global_toml.write_text("")

        project_toml = tmp_path / "devteam.toml"
        project_toml.write_text(
            '[project]\nname = "myproj"\nrepos = []\n[approval]\ncommit = "auto"\n'
        )

        config = load_and_merge_config(
            global_path=global_toml,
            project_path=project_toml,
        )
        assert config is not None
        assert config.approval.commit == "auto"


class TestSingleJobEnforcement:
    def test_raises_when_active_job_exists(self, runtime_store: RuntimeStateStore) -> None:
        from devteam.orchestrator.bootstrap import check_single_job

        runtime_store.register_job(
            workflow_id="existing-uuid",
            project_name="proj",
            repo_root="/tmp",
        )
        with pytest.raises(RuntimeError, match="active"):
            check_single_job(runtime_store)

    def test_passes_when_no_active_jobs(self, runtime_store: RuntimeStateStore) -> None:
        from devteam.orchestrator.bootstrap import check_single_job

        # No jobs — should not raise
        check_single_job(runtime_store)

    def test_passes_when_only_completed_jobs(self, runtime_store: RuntimeStateStore) -> None:
        from devteam.orchestrator.bootstrap import check_single_job

        runtime_store.register_job(
            workflow_id="old-uuid",
            project_name="proj",
            repo_root="/tmp",
        )
        runtime_store.update_job_status("W-1", "completed")
        check_single_job(runtime_store)


class TestJobAliasDurability:
    def test_alias_survives_reopen(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "durable.sqlite")
        s1 = RuntimeStateStore(db_path)
        job = s1.register_job(
            workflow_id="uuid-abc",
            project_name="proj",
            repo_root="/tmp",
        )
        assert job.alias == "W-1"
        s1.close()

        s2 = RuntimeStateStore(db_path)
        fetched = s2.get_job("W-1")
        assert fetched is not None
        assert fetched.workflow_id == "uuid-abc"
        s2.close()


class TestKnowledgeDegradation:
    @pytest.mark.asyncio
    async def test_knowledge_store_failure_returns_none(self) -> None:
        from devteam.orchestrator.bootstrap import try_connect_knowledge

        with patch(
            "devteam.orchestrator.bootstrap.KnowledgeStore",
            side_effect=ConnectionError("SurrealDB down"),
        ):
            store = await try_connect_knowledge(
                url="ws://localhost:8000",
                username="root",
                password="root",
            )
            assert store is None

    @pytest.mark.asyncio
    async def test_knowledge_connect_failure_returns_none(self) -> None:
        from devteam.orchestrator.bootstrap import try_connect_knowledge

        mock_store = MagicMock()
        mock_store.connect = AsyncMock(side_effect=ConnectionError("auth failed"))

        with patch(
            "devteam.orchestrator.bootstrap.KnowledgeStore",
            return_value=mock_store,
        ):
            store = await try_connect_knowledge(
                url="ws://localhost:8000",
                username="root",
                password="root",
            )
            assert store is None

    @pytest.mark.asyncio
    async def test_knowledge_success_returns_store(self) -> None:
        from devteam.orchestrator.bootstrap import try_connect_knowledge

        mock_store = MagicMock()
        mock_store.connect = AsyncMock()

        with patch(
            "devteam.orchestrator.bootstrap.KnowledgeStore",
            return_value=mock_store,
        ):
            store = await try_connect_knowledge(
                url="ws://localhost:8000",
                username="root",
                password="root",
            )
            assert store is mock_store


class TestEmbedderDegradation:
    def test_embedder_failure_returns_none(self) -> None:
        from devteam.orchestrator.bootstrap import try_create_embedder

        with patch(
            "devteam.orchestrator.bootstrap.create_embedder_from_config",
            side_effect=ImportError("ollama not available"),
        ):
            from devteam.config.settings import KnowledgeConfig

            embedder = try_create_embedder(KnowledgeConfig())
            assert embedder is None

    def test_embedder_success_returns_embedder(self) -> None:
        from devteam.orchestrator.bootstrap import try_create_embedder

        mock_embedder = MagicMock()
        with patch(
            "devteam.orchestrator.bootstrap.create_embedder_from_config",
            return_value=mock_embedder,
        ):
            from devteam.config.settings import KnowledgeConfig

            embedder = try_create_embedder(KnowledgeConfig())
            assert embedder is mock_embedder
