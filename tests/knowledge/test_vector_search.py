"""Tests for vector search and combined queries."""

import pytest
import pytest_asyncio

from devteam.knowledge.embeddings import EMBEDDING_DIMENSIONS
from devteam.knowledge.store import KnowledgeStore


@pytest_asyncio.fixture
async def populated_store():
    """Store with sample knowledge entries for search testing."""
    s = KnowledgeStore("mem://")
    await s.connect()

    # Create entries with distinct embeddings
    # Cluster 1: deployment-related (embeddings near [1.0, 0, 0, ...])
    deploy_emb = [1.0] + [0.0] * 767
    await s.create_entry(
        content="Fly.io requires HEALTHCHECK in Dockerfile",
        summary="Fly.io deployment checklist",
        tags=["shared", "cloud"],
        sharing="shared",
        project=None,
        embedding=deploy_emb,
        source={"agent": "cloud_engineer", "task": "T-1"},
    )
    await s.create_entry(
        content="Always set FLY_ALLOC_ID in production",
        summary="Fly.io env var",
        tags=["shared", "cloud"],
        sharing="shared",
        project=None,
        embedding=[0.9, 0.1] + [0.0] * 766,
        source={"agent": "cloud_engineer", "task": "T-2"},
    )

    # Cluster 2: ORM-related (embeddings near [0, 1.0, 0, ...])
    orm_emb = [0.0, 1.0] + [0.0] * 766
    await s.create_entry(
        content="This project uses Drizzle ORM, not Prisma",
        summary="ORM convention",
        tags=["project", "backend"],
        sharing="project",
        project="myapp",
        embedding=orm_emb,
    )

    # Cluster 3: process-related (embeddings near [0, 0, 1.0, ...])
    process_emb = [0.0, 0.0, 1.0] + [0.0] * 765
    await s.create_entry(
        content="CodeRabbit comments must be resolved before merge",
        summary="CodeRabbit process",
        tags=["process"],
        sharing="shared",
        project=None,
        embedding=process_emb,
    )

    yield s
    await s.close()


@pytest.mark.asyncio
class TestVectorSearch:
    async def test_search_returns_similar_entries(self, populated_store: KnowledgeStore):
        """Searching with a deployment-like embedding should return deployment entries."""
        query_emb = [0.95, 0.05] + [0.0] * 766
        results = await populated_store.vector_search(query_emb, limit=2)
        assert len(results) >= 1
        summaries = [r["summary"] for r in results]
        assert any("Fly.io" in s for s in summaries)

    async def test_search_respects_limit(self, populated_store: KnowledgeStore):
        query_emb = [0.5] * 768
        results = await populated_store.vector_search(query_emb, limit=1)
        assert len(results) <= 1

    async def test_search_with_sharing_filter(self, populated_store: KnowledgeStore):
        """Project-scoped entries should be filtered by sharing scope."""
        query_emb = [0.0, 1.0] + [0.0] * 766  # ORM-like
        results = await populated_store.vector_search(query_emb, limit=5, sharing="shared")
        for r in results:
            assert r["sharing"] == "shared"

    async def test_search_with_project_filter(self, populated_store: KnowledgeStore):
        query_emb = [0.0, 1.0] + [0.0] * 766
        results = await populated_store.vector_search(query_emb, limit=5, project="myapp")
        # Should include shared + project-scoped for myapp
        for r in results:
            assert r["sharing"] == "shared" or r["project"] == "myapp"

    async def test_search_excludes_superseded(self, populated_store: KnowledgeStore):
        """Superseded entries should be excluded from search results."""
        # Create an entry and supersede it
        old_id = await populated_store.create_entry(
            content="Old deployment advice",
            summary="Old deploy",
            tags=["shared"],
            sharing="shared",
            project=None,
            embedding=[0.95, 0.05] + [0.0] * 766,
        )
        new_id = await populated_store.create_entry(
            content="Updated deployment advice",
            summary="New deploy",
            tags=["shared"],
            sharing="shared",
            project=None,
            embedding=[0.96, 0.04] + [0.0] * 766,
        )
        await populated_store.add_relationship(new_id, "supersedes", old_id)

        query_emb = [0.95, 0.05] + [0.0] * 766
        results = await populated_store.vector_search(query_emb, limit=10, exclude_superseded=True)
        result_ids = [str(r["id"]) for r in results]
        assert old_id not in result_ids

    async def test_search_empty_store(self):
        s = KnowledgeStore("mem://")
        await s.connect()
        results = await s.vector_search([0.1] * 768, limit=5)
        assert results == []
        await s.close()

    async def test_search_validates_embedding_dimensions_too_short(
        self, populated_store: KnowledgeStore
    ):
        with pytest.raises(
            ValueError, match=f"Search vector must be {EMBEDDING_DIMENSIONS} dimensions, got 10"
        ):
            await populated_store.vector_search([0.1] * 10, limit=5)

    async def test_search_validates_embedding_dimensions_too_long(
        self, populated_store: KnowledgeStore
    ):
        with pytest.raises(
            ValueError, match=f"Search vector must be {EMBEDDING_DIMENSIONS} dimensions, got 1024"
        ):
            await populated_store.vector_search([0.1] * 1024, limit=5)

    async def test_superseded_exclusion_does_not_reduce_result_count(
        self, populated_store: KnowledgeStore
    ):
        """When top-N includes superseded entries, the result set should still
        contain the requested number of non-superseded entries (when available).

        This verifies superseded exclusion happens in the WHERE clause (pre-LIMIT)
        rather than as post-filtering.
        """
        # Create 3 entries with very similar embeddings in the same cluster
        base_emb = [0.0, 0.0, 0.0, 1.0] + [0.0] * 764
        ids = []
        for i in range(3):
            eid = await populated_store.create_entry(
                content=f"Supersede test entry {i}",
                summary=f"supersede-test-{i}",
                tags=["test"],
                sharing="shared",
                project=None,
                embedding=base_emb,
            )
            ids.append(eid)

        # Supersede the first entry (ids[0]) with the second (ids[1])
        await populated_store.add_relationship(ids[1], "supersedes", ids[0])

        # Search for limit=2 with that cluster's embedding
        results = await populated_store.vector_search(
            base_emb,
            limit=2,
            exclude_superseded=True,
            tags=["test"],
        )

        # The superseded entry should be excluded
        result_ids = [str(r["id"]) for r in results]
        assert ids[0] not in result_ids

        # We should still get 2 results (ids[1] and ids[2]) since
        # exclusion happens before the limit
        assert len(results) == 2


@pytest.mark.asyncio
class TestCombinedQueries:
    async def test_search_with_tag_filter(self, populated_store: KnowledgeStore):
        query_emb = [0.5] * 768
        results = await populated_store.vector_search(query_emb, limit=10, tags=["cloud"])
        for r in results:
            assert "cloud" in r["tags"]

    async def test_search_with_role_scope(self, populated_store: KnowledgeStore):
        """Agent role scoping: entries tagged for a specific role."""
        query_emb = [0.5] * 768
        results = await populated_store.vector_search(query_emb, limit=10, tags=["backend"])
        for r in results:
            assert "backend" in r["tags"]

    async def test_stats_by_scope(self, populated_store: KnowledgeStore):
        stats = await populated_store.get_stats_detailed()
        assert stats["total"] == 4
        assert stats["by_sharing"]["shared"] == 3
        assert stats["by_sharing"]["project"] == 1
        assert "myapp" in stats["by_project"]
