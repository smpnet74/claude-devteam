"""Tests for memory index generation."""

import pytest
import pytest_asyncio

from devteam.knowledge.index import MemoryIndexBuilder
from devteam.knowledge.store import KnowledgeStore


@pytest_asyncio.fixture
async def store_with_knowledge():
    """Store populated with diverse knowledge for index testing."""
    s = KnowledgeStore("mem://")
    await s.connect()

    await s.create_entry(
        content="Fly.io requires HEALTHCHECK in Dockerfile",
        summary="Fly.io deployment checklist",
        tags=["shared", "cloud"],
        sharing="shared",
        project=None,
        embedding=[0.1] * 768,
    )
    await s.create_entry(
        content="Always set FLY_ALLOC_ID in production",
        summary="Fly.io env var requirement",
        tags=["shared", "cloud"],
        sharing="shared",
        project=None,
        embedding=[0.11] * 768,
    )
    await s.create_entry(
        content="CodeRabbit comments must be resolved before merge",
        summary="CodeRabbit resolution process",
        tags=["process"],
        sharing="shared",
        project=None,
        embedding=[0.2] * 768,
    )
    await s.create_entry(
        content="This project uses Drizzle ORM, not Prisma",
        summary="ORM convention",
        tags=["project", "backend"],
        sharing="project",
        project="myapp",
        embedding=[0.3] * 768,
    )
    await s.create_entry(
        content="Auth uses OAuth2+PKCE flow",
        summary="Auth flow",
        tags=["project", "backend"],
        sharing="project",
        project="myapp",
        embedding=[0.31] * 768,
    )
    await s.create_entry(
        content="Other project uses REST",
        summary="API style",
        tags=["project", "backend"],
        sharing="project",
        project="otherapp",
        embedding=[0.4] * 768,
    )

    yield s
    await s.close()


@pytest.mark.asyncio
class TestMemoryIndexBuilder:
    async def test_build_index(self, store_with_knowledge):
        builder = MemoryIndexBuilder(store_with_knowledge)
        index = await builder.build(project="myapp")
        assert "Available Knowledge" in index
        assert "cloud" in index.lower() or "Fly.io" in index
        assert "CodeRabbit" in index or "process" in index.lower()

    async def test_index_includes_project_scoped(self, store_with_knowledge):
        builder = MemoryIndexBuilder(store_with_knowledge)
        index = await builder.build(project="myapp")
        assert "myapp" in index
        # Should NOT include otherapp knowledge
        assert "otherapp" not in index

    async def test_index_is_compact(self, store_with_knowledge):
        builder = MemoryIndexBuilder(store_with_knowledge)
        index = await builder.build(project="myapp")
        lines = [line for line in index.strip().split("\n") if line.strip()]
        # Index should be compact -- topic summaries, not full content
        assert len(lines) <= 50

    async def test_index_groups_by_section(self, store_with_knowledge):
        builder = MemoryIndexBuilder(store_with_knowledge)
        index = await builder.build(project="myapp")
        # Should have section headers for shared knowledge (Platform, Process, or Shared)
        has_shared_section = any(s in index for s in ("Platform", "Process", "Shared"))
        assert has_shared_section

    async def test_empty_store_returns_minimal_index(self):
        s = KnowledgeStore("mem://")
        await s.connect()
        try:
            builder = MemoryIndexBuilder(s)
            index = await builder.build(project="myapp")
            assert "Available Knowledge" in index
            assert "No knowledge" in index or len(index.strip().split("\n")) <= 5
        finally:
            await s.close()

    async def test_index_shows_entry_counts(self, store_with_knowledge):
        builder = MemoryIndexBuilder(store_with_knowledge)
        index = await builder.build(project="myapp")
        # Should show counts like "(2 entries)"
        assert "entr" in index.lower()

    async def test_index_uses_store_list_entries(self, store_with_knowledge):
        """Index builder should go through store.list_entries, not raw db.query."""
        builder = MemoryIndexBuilder(store_with_knowledge)
        # Patch list_entries to verify it is called
        original = store_with_knowledge.list_entries
        call_args: list = []

        async def tracking_list_entries(**kwargs):
            call_args.append(kwargs)
            return await original(**kwargs)

        store_with_knowledge.list_entries = tracking_list_entries
        await builder.build(project="myapp")
        assert len(call_args) == 1, "Expected list_entries to be called exactly once"
        assert call_args[0].get("project") == "myapp"

    async def test_index_stays_bounded_with_many_entries(self):
        """Index topics per section are capped at 10 even with many entries."""
        s = KnowledgeStore("mem://")
        await s.connect()

        try:
            # Insert 25 distinct shared entries to exceed the 10-topic cap
            for i in range(25):
                await s.create_entry(
                    content=f"Knowledge item number {i}",
                    summary=f"Topic {i}",
                    tags=["shared"],
                    sharing="shared",
                    project=None,
                    embedding=[0.01 * (i + 1)] * 768,
                )

            builder = MemoryIndexBuilder(s)
            index = await builder.build(project="myapp")

            # Should contain the overflow marker
            assert "... and" in index
            assert "more topics" in index

            # Scope bullet count to the Shared section only (not the entire output).
            # Extract lines between the **Shared:** header and the next blank line.
            lines = index.split("\n")
            shared_bullets: list[str] = []
            in_shared = False
            for line in lines:
                if line.startswith("**Shared:**"):
                    in_shared = True
                    continue
                if in_shared:
                    if line.strip() == "":
                        break
                    if line.startswith("- "):
                        shared_bullets.append(line)

            assert len(shared_bullets) <= 11  # 10 topics + 1 overflow
        finally:
            await s.close()
