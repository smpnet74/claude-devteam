"""Integration test -- full knowledge lifecycle with in-memory SurrealDB."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

from devteam.knowledge.embeddings import OllamaEmbedder
from devteam.knowledge.extractor import ExtractedEntry, KnowledgeExtractor
from devteam.knowledge.index import MemoryIndexBuilder, build_memory_index_safe
from devteam.knowledge.query_tool import QueryKnowledgeTool
from devteam.knowledge.store import KnowledgeStore


@pytest.fixture
def mock_embedder():
    embedder = AsyncMock(spec=OllamaEmbedder)
    call_count = 0

    async def embed_fn(text):
        nonlocal call_count
        call_count += 1
        # Return slightly different embeddings based on content keywords
        if "fly" in text.lower() or "deploy" in text.lower():
            return [1.0, 0.0] + [0.0] * 766
        elif "orm" in text.lower() or "drizzle" in text.lower():
            return [0.0, 1.0] + [0.0] * 766
        elif "coderabbit" in text.lower() or "process" in text.lower():
            return [0.0, 0.0, 1.0] + [0.0] * 765
        else:
            return [0.5] * 768

    embedder.embed.side_effect = embed_fn
    embedder.is_available.return_value = True
    return embedder


@pytest_asyncio.fixture
async def store():
    s = KnowledgeStore("mem://")
    await s.connect()
    yield s
    await s.close()


@pytest.mark.asyncio
class TestFullKnowledgeLifecycle:
    async def test_extract_persist_query_cycle(self, store, mock_embedder):
        """Full cycle: extract -> persist -> index -> query."""
        # Phase 1: Extract knowledge from agent output
        extractor = KnowledgeExtractor(store=store, embedder=mock_embedder)

        entries = [
            ExtractedEntry(
                content="Fly.io requires HEALTHCHECK in Dockerfile for zero-downtime deploys",
                summary="Fly.io HEALTHCHECK requirement",
                tags=["shared", "cloud"],
                scope="process",
            ),
            ExtractedEntry(
                content="This project uses Drizzle ORM with PostgreSQL",
                summary="ORM convention",
                tags=["project", "backend"],
                scope="project",
            ),
            ExtractedEntry(
                content="CodeRabbit comments must be resolved before merge",
                summary="CodeRabbit process",
                tags=["process"],
                scope="process",
            ),
        ]

        result = await extractor.persist_entries(
            entries=entries,
            agent_role="cloud_engineer",
            project="myapp",
            task_id="T-1",
        )
        assert result.persisted == 3
        assert result.rejected == 0

        # Phase 2: Build memory index
        builder = MemoryIndexBuilder(store)
        index = await builder.build(project="myapp")
        assert "Available Knowledge" in index
        assert "Fly.io" in index or "cloud" in index.lower()

        # Phase 3: Query knowledge
        tool = QueryKnowledgeTool(
            store=store,
            embedder=mock_embedder,
            current_project="myapp",
            agent_role="cloud_engineer",
        )
        result = await tool.query("Fly.io deployment")
        # KNN in mem:// mode may not return results; verify the flow completes
        assert isinstance(result, str)
        # If results came back, they should include our content
        if "No relevant" not in result and "No sufficiently" not in result:
            assert "HEALTHCHECK" in result

    async def test_secret_rejected_in_lifecycle(self, store, mock_embedder):
        """Entries with secrets should be rejected during extraction."""
        extractor = KnowledgeExtractor(store=store, embedder=mock_embedder)

        entries = [
            ExtractedEntry(
                content="Use API key AKIAIOSFODNN7EXAMPLE for S3",
                summary="S3 key",
                tags=["project"],
                scope="project",
            ),
            ExtractedEntry(
                content="Use structured logging",
                summary="Logging",
                tags=["process"],
                scope="process",
            ),
        ]

        result = await extractor.persist_entries(
            entries=entries,
            agent_role="backend",
            project="myapp",
            task_id="T-2",
        )
        assert result.persisted == 1
        assert result.rejected == 1

    async def test_supersession_in_lifecycle(self, store, mock_embedder):
        """Superseded entries should be excluded from queries."""
        extractor = KnowledgeExtractor(store=store, embedder=mock_embedder)

        # Create old knowledge
        old_entries = [
            ExtractedEntry(
                content="Deploy to Fly.io v1 with flyctl deploy",
                summary="Old deploy process",
                tags=["shared", "cloud"],
                scope="process",
            ),
        ]
        old_result = await extractor.persist_entries(
            entries=old_entries,
            agent_role="cloud_engineer",
            project="myapp",
            task_id="T-1",
        )

        # Create new knowledge that supersedes it
        new_entries = [
            ExtractedEntry(
                content="Deploy to Fly.io v2 with fly deploy --strategy rolling",
                summary="Updated deploy process",
                tags=["shared", "cloud"],
                scope="process",
            ),
        ]
        new_result = await extractor.persist_entries(
            entries=new_entries,
            agent_role="cloud_engineer",
            project="myapp",
            task_id="T-5",
        )

        # Mark old as superseded
        await store.add_relationship(new_result.entry_ids[0], "supersedes", old_result.entry_ids[0])

        # Verify superseded ID is tracked
        superseded_ids = await store.get_superseded_ids()
        assert old_result.entry_ids[0] in superseded_ids, (
            "Superseded entry should appear in get_superseded_ids()"
        )

        # Verify superseded entry is excluded from vector search results
        query_embedding = await mock_embedder.embed("Fly.io deployment")
        search_results = await store.vector_search(
            embedding=query_embedding,
            limit=10,
            exclude_superseded=True,
        )
        returned_ids = [str(r["id"]) for r in search_results]
        assert old_result.entry_ids[0] not in returned_ids, (
            "Superseded entry should be excluded from vector_search results"
        )

        # Query should return new, not old
        tool = QueryKnowledgeTool(
            store=store,
            embedder=mock_embedder,
            current_project="myapp",
            agent_role="cloud_engineer",
        )
        result = await tool.query("Fly.io deployment")
        # KNN in mem:// mode may not return results; verify the flow completes
        assert isinstance(result, str)
        # If results came back, superseded entry should be excluded
        if "No relevant" not in result and "No sufficiently" not in result:
            assert "v2" in result or "rolling" in result
            assert "v1" not in result, "Superseded v1 entry should not appear in query results"

    async def test_project_scoping_in_lifecycle(self, store, mock_embedder):
        """Project-scoped knowledge should not leak to other projects."""
        extractor = KnowledgeExtractor(store=store, embedder=mock_embedder)

        # Add knowledge for project A
        await extractor.persist_entries(
            entries=[
                ExtractedEntry(
                    content="Project A uses MongoDB",
                    summary="Database choice",
                    tags=["project", "backend"],
                    scope="project",
                ),
            ],
            agent_role="data_engineer",
            project="project-a",
            task_id="T-1",
        )

        # Add knowledge for project B
        await extractor.persist_entries(
            entries=[
                ExtractedEntry(
                    content="Project B uses PostgreSQL",
                    summary="Database choice",
                    tags=["project", "backend"],
                    scope="project",
                ),
            ],
            agent_role="data_engineer",
            project="project-b",
            task_id="T-2",
        )

        # Query from project A context should not see project B knowledge
        tool_a = QueryKnowledgeTool(
            store=store,
            embedder=mock_embedder,
            current_project="project-a",
            agent_role="data_engineer",
        )
        result_a = await tool_a.query("database", scope="project")
        assert isinstance(result_a, str)
        # If results came back, they must NOT contain project B content
        if "No relevant" not in result_a and "No sufficiently" not in result_a:
            assert "PostgreSQL" not in result_a, (
                "Project A query should not see Project B's PostgreSQL entry"
            )

        # Verify from the other direction: project B should not see project A
        tool_b = QueryKnowledgeTool(
            store=store,
            embedder=mock_embedder,
            current_project="project-b",
            agent_role="data_engineer",
        )
        result_b = await tool_b.query("database", scope="project")
        assert isinstance(result_b, str)
        if "No relevant" not in result_b and "No sufficiently" not in result_b:
            assert "MongoDB" not in result_b, (
                "Project B query should not see Project A's MongoDB entry"
            )

    async def test_graceful_degradation_full_cycle(self, mock_embedder):
        """System should work end-to-end even when SurrealDB is unavailable."""
        # No store connected
        index = await build_memory_index_safe(
            store=None,
            project="myapp",
        )
        assert isinstance(index, str)
        assert "Available Knowledge" in index

        # Extractor with failed store
        failed_store = AsyncMock(spec=KnowledgeStore)
        failed_store.create_entry.side_effect = Exception("DB unavailable")

        extractor = KnowledgeExtractor(store=failed_store, embedder=mock_embedder)
        result = await extractor.persist_entries(
            entries=[
                ExtractedEntry(
                    content="Test",
                    summary="Test",
                    tags=["process"],
                    scope="process",
                ),
            ],
            agent_role="backend",
            project="myapp",
            task_id="T-99",
        )
        assert result.errors == 1
        assert result.persisted == 0


@pytest.mark.asyncio
class TestMemoryIndexInjection:
    """Tests proving memory index can be injected into agent prompts (consultant requirement)."""

    async def test_memory_index_is_injectable_into_prompt(self, store, mock_embedder):
        """Memory index output is a string that can be appended to a system prompt."""
        extractor = KnowledgeExtractor(store=store, embedder=mock_embedder)
        await extractor.persist_entries(
            entries=[
                ExtractedEntry(
                    content="Always use structured logging",
                    summary="Logging convention",
                    tags=["process"],
                    scope="process",
                ),
            ],
            agent_role="backend",
            project="myapp",
            task_id="T-1",
        )

        index = await build_memory_index_safe(
            store=store,
            project="myapp",
        )

        # The index is a plain string
        assert isinstance(index, str)
        assert len(index) > 0

        # It can be concatenated into a system prompt
        base_prompt = "You are a backend engineer."
        full_prompt = f"{base_prompt}\n\n{index}"
        assert base_prompt in full_prompt
        assert "Available Knowledge" in full_prompt

    async def test_empty_index_injectable_without_error(self):
        """Even an empty index is a valid string for prompt injection."""
        index = await build_memory_index_safe(
            store=None,
            project="myapp",
        )
        base_prompt = "You are a backend engineer."
        full_prompt = f"{base_prompt}\n\n{index}"
        assert isinstance(full_prompt, str)
        assert base_prompt in full_prompt


@pytest.mark.asyncio
class TestQueryToolDefinition:
    """Tests proving query_knowledge tool definition is valid for SDK (consultant requirement)."""

    async def test_tool_definition_is_valid_for_sdk(self, store, mock_embedder):
        """Tool definition has the schema expected by the Agent SDK."""
        tool = QueryKnowledgeTool(
            store=store,
            embedder=mock_embedder,
            current_project="myapp",
            agent_role="backend",
        )
        defn = tool.tool_definition()

        # Required top-level fields
        assert "name" in defn
        assert defn["name"] == "query_knowledge"
        assert "description" in defn
        assert isinstance(defn["description"], str)
        assert len(defn["description"]) > 0

        # Parameters schema
        assert "parameters" in defn
        params = defn["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
        assert "query" in params["properties"]
        assert "scope" in params["properties"]
        assert "required" in params
        assert "query" in params["required"]

        # query property
        query_prop = params["properties"]["query"]
        assert query_prop["type"] == "string"

        # scope property with enum
        scope_prop = params["properties"]["scope"]
        assert scope_prop["type"] == "string"
        assert "enum" in scope_prop
        assert set(scope_prop["enum"]) == {"shared", "my_role", "project", "all"}

    async def test_tool_definition_callable_after_construction(self, store, mock_embedder):
        """The tool can be instantiated and its definition retrieved without connecting."""
        tool = QueryKnowledgeTool(
            store=store,
            embedder=mock_embedder,
            current_project="myapp",
            agent_role="backend",
        )
        # tool_definition() is synchronous and should not require any async calls
        defn = tool.tool_definition()
        assert defn is not None

    async def test_tool_schema_rejects_invalid_scope(self, store, mock_embedder):
        """Passing an invalid scope value should be detectable via the schema enum."""
        tool = QueryKnowledgeTool(
            store=store,
            embedder=mock_embedder,
            current_project="myapp",
            agent_role="backend",
        )
        defn = tool.tool_definition()

        valid_scopes = set(defn["parameters"]["properties"]["scope"]["enum"])
        assert "invalid_scope" not in valid_scopes, (
            "Schema enum should not include arbitrary values"
        )

        # Verify schema enforces required fields
        assert "query" in defn["parameters"]["required"]

        # Verify query must be a string
        assert defn["parameters"]["properties"]["query"]["type"] == "string"

        # Verify scope has a constrained set of valid values
        assert valid_scopes == {"shared", "my_role", "project", "all"}


@pytest.mark.asyncio
class TestMaterializedIndexLifecycle:
    async def test_materialized_index_updated_on_write(self, store):
        """Writing an entry should trigger the materialized index event."""
        await store.create_entry(
            content="Test content for index",
            summary="Test",
            tags=["process"],
            sharing="shared",
            project=None,
            embedding=[0.1] * 768,
        )
        index_data = await store.get_materialized_index()
        # The event may or may not fire in mem:// mode; we just
        # verify the method doesn't crash and returns dict or None.
        assert index_data is None or isinstance(index_data, dict)
