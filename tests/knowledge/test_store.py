"""Tests for SurrealDB knowledge store -- connection, CRUD, and graph relationships."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

from devteam.knowledge.embeddings import EMBEDDING_DIMENSIONS
from devteam.knowledge.store import KnowledgeStore, VALID_RELATIONS, _UPDATABLE_FIELDS


@pytest_asyncio.fixture
async def store():
    """Create an in-memory KnowledgeStore for testing."""
    s = KnowledgeStore("mem://")
    await s.connect()
    yield s
    await s.close()


@pytest.mark.asyncio
class TestKnowledgeStoreConnection:
    async def test_connect_and_close(self):
        s = KnowledgeStore("mem://")
        await s.connect()
        assert s.is_connected
        await s.close()
        assert not s.is_connected

    async def test_schema_initialized_on_connect(self, store: KnowledgeStore):
        """Schema tables and indexes should exist after connect."""
        result = await store.db.query("INFO FOR TABLE knowledge")
        assert result is not None
        assert isinstance(result, dict)
        assert "fields" in result
        assert "content" in result["fields"]

    async def test_multiple_connect_is_idempotent(self):
        """Calling connect twice should not raise."""
        s = KnowledgeStore("mem://")
        await s.connect()
        await s.connect()  # should not raise
        assert s.is_connected
        await s.close()

    async def test_connect_calls_signin_when_credentials_provided(self):
        """signin should be called when username and password are provided."""
        s = KnowledgeStore("ws://localhost:8000")
        mock_db = AsyncMock()
        s.db = mock_db

        await s.connect(username="root", password="root")

        mock_db.connect.assert_awaited_once()
        mock_db.signin.assert_awaited_once_with({"username": "root", "password": "root"})
        mock_db.use.assert_awaited_once_with("devteam", "knowledge")

    async def test_connect_skips_signin_without_credentials(self):
        """signin should NOT be called when no credentials are provided (mem:// mode)."""
        s = KnowledgeStore("mem://")
        mock_db = AsyncMock()
        s.db = mock_db

        await s.connect()

        mock_db.connect.assert_awaited_once()
        mock_db.signin.assert_not_awaited()
        mock_db.use.assert_awaited_once_with("devteam", "knowledge")

    async def test_connect_with_custom_namespace_and_database(self):
        """Custom namespace and database should be passed to use()."""
        s = KnowledgeStore("mem://")
        mock_db = AsyncMock()
        s.db = mock_db

        await s.connect(namespace="custom_ns", database="custom_db")

        mock_db.use.assert_awaited_once_with("custom_ns", "custom_db")


@pytest.mark.asyncio
class TestKnowledgeStoreCRUD:
    async def test_create_entry(self, store: KnowledgeStore):
        entry_id = await store.create_entry(
            content="CodeRabbit comments must be resolved before merge",
            summary="CodeRabbit resolution requirement",
            tags=["process", "coderabbit"],
            sharing="shared",
            project=None,
            embedding=[0.1] * 768,
        )
        assert entry_id is not None
        assert "knowledge:" in entry_id

    async def test_create_entry_validates_empty_content(self, store: KnowledgeStore):
        with pytest.raises(ValueError, match="content must not be empty"):
            await store.create_entry(
                content="",
                summary="empty",
                tags=[],
                sharing="shared",
                project=None,
                embedding=[0.0] * 768,
            )

    async def test_create_entry_validates_sharing(self, store: KnowledgeStore):
        with pytest.raises(ValueError, match="sharing must be"):
            await store.create_entry(
                content="test",
                summary="test",
                tags=[],
                sharing="invalid",
                project=None,
                embedding=[0.0] * 768,
            )

    async def test_create_entry_validates_project_required(self, store: KnowledgeStore):
        with pytest.raises(ValueError, match="project must be set"):
            await store.create_entry(
                content="test",
                summary="test",
                tags=[],
                sharing="project",
                project=None,
                embedding=[0.0] * 768,
            )

    async def test_create_entry_validates_embedding_too_short(self, store: KnowledgeStore):
        with pytest.raises(
            ValueError, match=f"Embedding must be {EMBEDDING_DIMENSIONS} dimensions, got 10"
        ):
            await store.create_entry(
                content="test",
                summary="test",
                tags=[],
                sharing="shared",
                project=None,
                embedding=[0.0] * 10,
            )

    async def test_create_entry_validates_embedding_too_long(self, store: KnowledgeStore):
        with pytest.raises(
            ValueError, match=f"Embedding must be {EMBEDDING_DIMENSIONS} dimensions, got 1024"
        ):
            await store.create_entry(
                content="test",
                summary="test",
                tags=[],
                sharing="shared",
                project=None,
                embedding=[0.0] * 1024,
            )

    async def test_get_entry(self, store: KnowledgeStore):
        entry_id = await store.create_entry(
            content="Use Drizzle ORM, not Prisma",
            summary="ORM convention",
            tags=["project", "backend"],
            sharing="project",
            project="myapp",
            embedding=[0.2] * 768,
        )
        entry = await store.get_entry(entry_id)
        assert entry is not None
        assert entry["content"] == "Use Drizzle ORM, not Prisma"
        assert entry["sharing"] == "project"
        assert entry["project"] == "myapp"
        assert entry["verified"] is False
        assert entry["access_count"] == 0

    async def test_get_nonexistent_entry_returns_none(self, store: KnowledgeStore):
        result = await store.get_entry("knowledge:nonexistent")
        assert result is None

    async def test_update_entry_verified(self, store: KnowledgeStore):
        entry_id = await store.create_entry(
            content="Test content",
            summary="Test",
            tags=["process"],
            sharing="shared",
            project=None,
            embedding=[0.3] * 768,
        )
        await store.update_entry(entry_id, verified=True)
        entry = await store.get_entry(entry_id)
        assert entry is not None
        assert entry["verified"] is True

    async def test_increment_access_count(self, store: KnowledgeStore):
        entry_id = await store.create_entry(
            content="Test content",
            summary="Test",
            tags=["process"],
            sharing="shared",
            project=None,
            embedding=[0.4] * 768,
        )
        await store.increment_access_count(entry_id)
        await store.increment_access_count(entry_id)
        entry = await store.get_entry(entry_id)
        assert entry is not None
        assert entry["access_count"] == 2

    async def test_delete_entry(self, store: KnowledgeStore):
        entry_id = await store.create_entry(
            content="To be deleted",
            summary="Delete me",
            tags=["process"],
            sharing="shared",
            project=None,
            embedding=[0.5] * 768,
        )
        await store.delete_entry(entry_id)
        entry = await store.get_entry(entry_id)
        assert entry is None

    async def test_delete_by_project(self, store: KnowledgeStore):
        await store.create_entry(
            content="Project A knowledge",
            summary="A",
            tags=["project"],
            sharing="project",
            project="project-a",
            embedding=[0.6] * 768,
        )
        await store.create_entry(
            content="Project B knowledge",
            summary="B",
            tags=["project"],
            sharing="project",
            project="project-b",
            embedding=[0.7] * 768,
        )
        deleted_count = await store.delete_by_project("project-a")
        assert deleted_count == 1
        stats = await store.get_stats()
        assert stats["total"] == 1


@pytest.mark.asyncio
class TestGraphRelationships:
    async def test_create_discovered_relationship(self, store: KnowledgeStore):
        entry_id = await store.create_entry(
            content="Fly.io needs HEALTHCHECK",
            summary="Fly.io deployment",
            tags=["shared", "cloud"],
            sharing="shared",
            project=None,
            embedding=[0.1] * 768,
        )
        await store.add_relationship(
            from_id="agent:cloud_engineer",
            relation="discovered",
            to_id=entry_id,
        )
        rels = await store.get_relationships(entry_id, direction="in", relation="discovered")
        assert len(rels) >= 1

    async def test_create_supersedes_relationship(self, store: KnowledgeStore):
        old_id = await store.create_entry(
            content="Use npm v8",
            summary="npm version",
            tags=["process"],
            sharing="shared",
            project=None,
            embedding=[0.2] * 768,
        )
        new_id = await store.create_entry(
            content="Use npm v10 (v8 is EOL)",
            summary="npm version updated",
            tags=["process"],
            sharing="shared",
            project=None,
            embedding=[0.21] * 768,
        )
        await store.add_relationship(new_id, "supersedes", old_id)
        superseded = await store.get_superseded_ids()
        assert old_id in superseded

    async def test_create_requires_relationship(self, store: KnowledgeStore):
        prereq_id = await store.create_entry(
            content="PostgreSQL must be running",
            summary="PostgreSQL prerequisite",
            tags=["process"],
            sharing="shared",
            project=None,
            embedding=[0.3] * 768,
        )
        entry_id = await store.create_entry(
            content="Run migrations with alembic upgrade head",
            summary="Migration command",
            tags=["process"],
            sharing="shared",
            project=None,
            embedding=[0.31] * 768,
        )
        await store.add_relationship(entry_id, "requires", prereq_id)
        reqs = await store.get_relationships(entry_id, direction="out", relation="requires")
        assert len(reqs) >= 1

    async def test_create_relates_to_relationship(self, store: KnowledgeStore):
        id1 = await store.create_entry(
            content="REST API uses JSON:API format",
            summary="API format",
            tags=["project", "backend"],
            sharing="project",
            project="myapp",
            embedding=[0.4] * 768,
        )
        id2 = await store.create_entry(
            content="Frontend fetches via tanstack-query",
            summary="Data fetching",
            tags=["project", "frontend"],
            sharing="project",
            project="myapp",
            embedding=[0.41] * 768,
        )
        await store.add_relationship(id1, "relates_to", id2)
        rels = await store.get_relationships(id1, direction="out", relation="relates_to")
        assert len(rels) >= 1

    async def test_invalid_relation_raises(self, store: KnowledgeStore):
        with pytest.raises(ValueError, match="Invalid relation"):
            await store.add_relationship("knowledge:a", "invalid_rel", "knowledge:b")

    async def test_valid_relations_constant(self):
        assert VALID_RELATIONS == {"discovered", "supersedes", "requires", "relates_to"}


@pytest.mark.asyncio
class TestConnectDegradation:
    async def test_connect_failure_raises_connection_error(self):
        """Failed connect wraps the underlying exception in ConnectionError."""
        s = KnowledgeStore("ws://localhost:9999")
        mock_db = AsyncMock()
        mock_db.connect.side_effect = OSError("Connection refused")
        s.db = mock_db

        with pytest.raises(ConnectionError, match="Failed to connect to SurrealDB"):
            await s.connect()
        assert not s.is_connected

    async def test_connect_failure_preserves_disconnected_state(self):
        """After a failed connect, is_connected remains False."""
        s = KnowledgeStore("ws://localhost:9999")
        mock_db = AsyncMock()
        mock_db.connect.side_effect = RuntimeError("boom")
        s.db = mock_db

        with pytest.raises(ConnectionError):
            await s.connect()
        assert s.is_connected is False


@pytest.mark.asyncio
class TestEmptyEmbeddingValidation:
    async def test_create_entry_rejects_empty_embedding(self, store: KnowledgeStore):
        """An empty embedding list should fail dimension validation."""
        with pytest.raises(
            ValueError, match=f"Embedding must be {EMBEDDING_DIMENSIONS} dimensions, got 0"
        ):
            await store.create_entry(
                content="test",
                summary="test",
                tags=[],
                sharing="shared",
                project=None,
                embedding=[],
            )


@pytest.mark.asyncio
class TestUpdateFieldAllowlist:
    async def test_update_with_valid_field(self, store: KnowledgeStore):
        entry_id = await store.create_entry(
            content="Test content",
            summary="Test",
            tags=["process"],
            sharing="shared",
            project=None,
            embedding=[0.1] * 768,
        )
        await store.update_entry(entry_id, verified=True)
        entry = await store.get_entry(entry_id)
        assert entry is not None
        assert entry["verified"] is True

    async def test_update_rejects_invalid_field(self, store: KnowledgeStore):
        entry_id = await store.create_entry(
            content="Test content",
            summary="Test",
            tags=["process"],
            sharing="shared",
            project=None,
            embedding=[0.1] * 768,
        )
        with pytest.raises(ValueError, match="Cannot update fields"):
            await store.update_entry(entry_id, not_a_real_field="evil")

    async def test_updatable_fields_constant(self):
        assert _UPDATABLE_FIELDS == frozenset({
            "content", "summary", "tags", "sharing", "project",
            "embedding", "verified", "source", "access_count",
        })
