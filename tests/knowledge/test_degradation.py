"""Tests for graceful degradation when SurrealDB or Ollama is unavailable."""

import pytest
from unittest.mock import AsyncMock

from devteam.knowledge.embeddings import OllamaEmbedder
from devteam.knowledge.extractor import ExtractedEntry, KnowledgeExtractor
from devteam.knowledge.index import MemoryIndexBuilder, build_memory_index_safe
from devteam.knowledge.query_tool import QueryKnowledgeTool
from devteam.knowledge.store import KnowledgeStore


@pytest.mark.asyncio
class TestDegradedSurrealDB:
    async def test_index_builder_returns_empty_on_no_store(self):
        """When SurrealDB is unavailable (store=None), index returns empty/minimal."""
        result = await build_memory_index_safe(
            store=None,
            role="backend_engineer",
            project="myapp",
        )
        assert isinstance(result, str)
        assert "Available Knowledge" in result

    async def test_index_builder_returns_empty_on_disconnected_store(self):
        """When store exists but is not connected, returns empty index."""
        store = AsyncMock(spec=KnowledgeStore)
        store.is_connected = False

        result = await build_memory_index_safe(
            store=store,
            role="backend_engineer",
            project="myapp",
        )
        assert isinstance(result, str)
        assert "Available Knowledge" in result

    async def test_index_builder_handles_query_failure(self):
        """When a SurrealDB query fails, degrade gracefully."""
        store = AsyncMock(spec=KnowledgeStore)
        store.is_connected = True
        store.list_entries.side_effect = Exception("SurrealDB connection lost")

        builder = MemoryIndexBuilder(store)
        # Should not raise
        index = await builder.build(project="myapp")
        assert isinstance(index, str)
        assert "Available Knowledge" in index

    async def test_build_memory_index_safe_catches_all_errors(self):
        """build_memory_index_safe should never raise, even on unexpected errors."""
        store = AsyncMock(spec=KnowledgeStore)
        store.is_connected = True
        store.list_entries.side_effect = RuntimeError("Unexpected SurrealDB crash")

        result = await build_memory_index_safe(
            store=store,
            role="backend_engineer",
            project="myapp",
        )
        assert isinstance(result, str)

    async def test_extractor_skips_when_store_unavailable(self):
        """Extraction should skip gracefully when store is unavailable."""
        embedder = AsyncMock(spec=OllamaEmbedder)
        embedder.embed.return_value = [0.1] * 768

        store = AsyncMock(spec=KnowledgeStore)
        store.create_entry.side_effect = Exception("DB down")

        extractor = KnowledgeExtractor(store=store, embedder=embedder)
        entries = [
            ExtractedEntry(
                content="Test",
                summary="Test",
                tags=["process"],
                scope="process",
            ),
        ]
        result = await extractor.persist_entries(
            entries=entries,
            agent_role="backend_engineer",
            project="myapp",
            task_id="T-1",
        )
        # Should count as error, not crash
        assert result.errors == 1
        assert result.persisted == 0


@pytest.mark.asyncio
class TestDegradedOllama:
    async def test_query_tool_returns_message_on_ollama_down(self):
        store = AsyncMock(spec=KnowledgeStore)
        embedder = AsyncMock(spec=OllamaEmbedder)
        embedder.embed.side_effect = Exception("Connection refused")

        tool = QueryKnowledgeTool(
            store=store,
            embedder=embedder,
            current_project="myapp",
            agent_role="backend_engineer",
        )
        result = await tool.query("anything")
        assert "unavailable" in result.lower()

    async def test_extractor_skips_when_ollama_unavailable(self):
        store = AsyncMock(spec=KnowledgeStore)
        embedder = AsyncMock(spec=OllamaEmbedder)
        embedder.embed.side_effect = Exception("Ollama not running")

        extractor = KnowledgeExtractor(store=store, embedder=embedder)
        entries = [
            ExtractedEntry(
                content="Test learning",
                summary="Test",
                tags=["process"],
                scope="process",
            ),
        ]
        result = await extractor.persist_entries(
            entries=entries,
            agent_role="backend_engineer",
            project="myapp",
            task_id="T-1",
        )
        assert result.errors == 1
        assert result.persisted == 0


@pytest.mark.asyncio
class TestDegradedBothUnavailable:
    async def test_full_degradation_no_crash(self):
        """When both SurrealDB and Ollama are down, nothing crashes."""
        # Index: no store
        index = await build_memory_index_safe(store=None, project="myapp")
        assert isinstance(index, str)

        # Query tool: embedder fails
        store = AsyncMock(spec=KnowledgeStore)
        embedder = AsyncMock(spec=OllamaEmbedder)
        embedder.embed.side_effect = Exception("Ollama down")

        tool = QueryKnowledgeTool(
            store=store,
            embedder=embedder,
            current_project="myapp",
            agent_role="backend_engineer",
        )
        result = await tool.query("anything")
        assert isinstance(result, str)

        # Extractor: both fail
        store.create_entry.side_effect = Exception("DB down")
        extractor = KnowledgeExtractor(store=store, embedder=embedder)
        persist_result = await extractor.persist_entries(
            entries=[
                ExtractedEntry(
                    content="Test",
                    summary="Test",
                    tags=["process"],
                    scope="process",
                ),
            ],
            agent_role="backend_engineer",
            project="myapp",
            task_id="T-1",
        )
        assert persist_result.errors == 1
        assert persist_result.persisted == 0
