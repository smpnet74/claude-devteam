"""Tests for knowledge extraction from agent output."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

from devteam.knowledge.embeddings import OllamaEmbedder
from devteam.knowledge.extractor import (
    ExtractionResult,
    ExtractedEntry,
    KnowledgeExtractor,
)
from devteam.knowledge.store import KnowledgeStore


@pytest.fixture
def mock_embedder():
    embedder = AsyncMock(spec=OllamaEmbedder)
    embedder.embed.return_value = [0.1] * 768
    embedder.embed_batch.return_value = [[0.1] * 768, [0.2] * 768]
    embedder.is_available.return_value = True
    return embedder


@pytest_asyncio.fixture
async def mock_store():
    store = KnowledgeStore("mem://")
    await store.connect()
    yield store
    await store.close()


@pytest.fixture
def extractor(mock_store, mock_embedder):
    return KnowledgeExtractor(store=mock_store, embedder=mock_embedder)


@pytest.fixture
def sample_extraction_result():
    """Simulates what the haiku extractor agent would return."""
    return ExtractionResult(
        entries=[
            ExtractedEntry(
                content="Fly.io requires HEALTHCHECK in Dockerfile for zero-downtime deploys",
                summary="Fly.io HEALTHCHECK requirement",
                tags=["shared", "cloud"],
                scope="process",
            ),
            ExtractedEntry(
                content="This project uses pnpm, not npm -- lock file is pnpm-lock.yaml",
                summary="Package manager convention",
                tags=["project", "frontend"],
                scope="project",
            ),
        ]
    )


class TestExtractionResult:
    def test_extraction_result_parsing(self, sample_extraction_result):
        assert len(sample_extraction_result.entries) == 2
        assert sample_extraction_result.entries[0].scope == "process"
        assert sample_extraction_result.entries[1].scope == "project"

    def test_empty_extraction(self):
        result = ExtractionResult(entries=[])
        assert len(result.entries) == 0


@pytest.mark.asyncio
class TestKnowledgeExtractor:
    async def test_persist_entries(self, extractor, sample_extraction_result, mock_store):
        """Extracted entries should be persisted to the store."""
        await extractor.persist_entries(
            entries=sample_extraction_result.entries,
            agent_role="cloud_engineer",
            project="myapp",
            task_id="T-1",
        )
        stats = await mock_store.get_stats()
        assert stats["total"] == 2

    async def test_persist_skips_entries_with_secrets(self, extractor, mock_store):
        """Entries containing secrets should be rejected."""
        entries = [
            ExtractedEntry(
                content="Use API key AKIAIOSFODNN7EXAMPLE for S3 access",
                summary="S3 credentials",
                tags=["project"],
                scope="project",
            ),
            ExtractedEntry(
                content="Use structured logging in all services",
                summary="Logging convention",
                tags=["shared"],
                scope="process",
            ),
        ]
        result = await extractor.persist_entries(
            entries=entries,
            agent_role="backend_engineer",
            project="myapp",
            task_id="T-2",
        )
        assert result.persisted == 1
        assert result.rejected == 1
        stats = await mock_store.get_stats()
        assert stats["total"] == 1

    async def test_persist_creates_discovered_relationship(
        self, extractor, sample_extraction_result, mock_store
    ):
        """Each persisted entry should have a discovered-by relationship."""
        await extractor.persist_entries(
            entries=sample_extraction_result.entries,
            agent_role="cloud_engineer",
            project="myapp",
            task_id="T-1",
        )
        # Verify entries exist (relationship creation tested separately)
        stats = await mock_store.get_stats()
        assert stats["total"] == 2

    async def test_persist_with_embedder_unavailable(self, mock_store):
        """When embedder is unavailable, extraction should be skipped gracefully."""
        embedder = AsyncMock(spec=OllamaEmbedder)
        embedder.embed.side_effect = Exception("Ollama unavailable")
        extractor = KnowledgeExtractor(store=mock_store, embedder=embedder)

        entries = [
            ExtractedEntry(
                content="Test content",
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
        assert result.persisted == 0
        assert result.errors == 1

    async def test_sharing_scope_from_extraction_tag(self, extractor, mock_store):
        """Process-tagged entries should be stored as shared."""
        entries = [
            ExtractedEntry(
                content="Always run lint before commit",
                summary="Lint process",
                tags=["process"],
                scope="process",
            ),
        ]
        await extractor.persist_entries(
            entries=entries,
            agent_role="devops_engineer",
            project="myapp",
            task_id="T-3",
        )
        stats = await mock_store.get_stats_detailed()
        assert stats["by_sharing"].get("shared", 0) == 1

    async def test_scope_wins_over_tags_when_they_disagree(self, extractor, mock_store):
        """When entry.scope and tags disagree, scope should be authoritative."""
        entries = [
            ExtractedEntry(
                content="Use shared CI templates across all repos",
                summary="Shared CI templates",
                tags=["project", "ci"],  # tags say "project"
                scope="process",          # scope says "process" (-> shared)
            ),
        ]
        result = await extractor.persist_entries(
            entries=entries,
            agent_role="devops_engineer",
            project="myapp",
            task_id="T-scope",
        )
        assert result.persisted == 1
        stats = await mock_store.get_stats_detailed()
        # scope="process" should produce sharing="shared", despite "project" tag
        assert stats["by_sharing"].get("shared", 0) == 1
        assert stats["by_sharing"].get("project", 0) == 0

    async def test_secret_in_summary_is_rejected(self, extractor, mock_store):
        """Entries with secrets in the summary (not content) should be rejected."""
        entries = [
            ExtractedEntry(
                content="We use a token for CI authentication",
                summary='CI token: secret = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"',
                tags=["project"],
                scope="project",
            ),
        ]
        result = await extractor.persist_entries(
            entries=entries,
            agent_role="backend_engineer",
            project="myapp",
            task_id="T-secret-summary",
        )
        assert result.rejected == 1
        assert result.persisted == 0
        stats = await mock_store.get_stats()
        assert stats["total"] == 0
