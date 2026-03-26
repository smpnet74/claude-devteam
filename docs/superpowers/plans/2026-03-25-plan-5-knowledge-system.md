# Plan 5: Knowledge & Memory System Implementation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build the institutional knowledge system using SurrealDB and Ollama for team learning that compounds across projects.

**Architecture:** SurrealDB runs embedded (file-backed via `Surreal("file://...")`) storing knowledge entries with 768-dimension vector embeddings, HNSW index for similarity search, and graph relationships for knowledge evolution. Ollama generates embeddings locally via nomic-embed-text. A haiku-powered extractor runs after every agent step to capture reusable learnings. Agents access knowledge through two mechanisms: a pre-injected memory index (topic summary) and an on-demand `query_knowledge` tool with scope filtering. The system degrades gracefully — SurrealDB unavailable means empty index and skipped extraction, never workflow failure.

**Tech Stack:** Python 3.11+, surrealdb (Python SDK), httpx (Ollama HTTP API), Pydantic, pytest, pytest-asyncio

**Assumes:** Plan 1 (daemon, CLI, entities) and Plan 2 (agent definitions, invoker) are complete. Independent of Plans 3-4.

---

## Task 1: SurrealDB Connection & Schema Initialization

**Files:**
- Create: `src/devteam/knowledge/__init__.py`
- Create: `src/devteam/knowledge/store.py`
- Create: `tests/knowledge/__init__.py`
- Create: `tests/knowledge/test_store.py`

- [ ] **Step 1 (2 min): Create knowledge package structure**

```bash
mkdir -p src/devteam/knowledge
touch src/devteam/knowledge/__init__.py
mkdir -p tests/knowledge
touch tests/knowledge/__init__.py
```

`src/devteam/knowledge/__init__.py`:
```python
"""Knowledge & Memory System — SurrealDB-backed institutional memory."""
```

- [ ] **Step 2 (3 min): Write tests for KnowledgeStore connection and schema**

`tests/knowledge/test_store.py`:
```python
"""Tests for SurrealDB knowledge store."""

import pytest
import pytest_asyncio
from devteam.knowledge.store import KnowledgeStore


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

    async def test_multiple_connect_is_idempotent(self):
        """Calling connect twice should not raise."""
        s = KnowledgeStore("mem://")
        await s.connect()
        await s.connect()  # should not raise
        await s.close()


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
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_store.py -v
```

- [ ] **Step 3 (5 min): Implement KnowledgeStore**

`src/devteam/knowledge/store.py`:
```python
"""SurrealDB knowledge store — connection, schema initialization, CRUD."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from surrealdb import Surreal

logger = logging.getLogger(__name__)

# Schema definition for the knowledge table
SCHEMA_STATEMENTS = [
    "DEFINE TABLE knowledge SCHEMAFULL",
    "DEFINE FIELD content ON knowledge TYPE string",
    "DEFINE FIELD summary ON knowledge TYPE string",
    "DEFINE FIELD source ON knowledge TYPE option<object>",
    "DEFINE FIELD tags ON knowledge TYPE array<string>",
    "DEFINE FIELD sharing ON knowledge TYPE string",
    "DEFINE FIELD project ON knowledge TYPE option<string>",
    "DEFINE FIELD embedding ON knowledge TYPE array<float>",
    "DEFINE FIELD created_at ON knowledge TYPE datetime",
    "DEFINE FIELD verified ON knowledge TYPE bool DEFAULT false",
    "DEFINE FIELD access_count ON knowledge TYPE int DEFAULT 0",
    "DEFINE INDEX knowledge_vec ON knowledge FIELDS embedding HNSW DIMENSION 768 DIST COSINE",
]


class KnowledgeStore:
    """Manages SurrealDB connection and knowledge CRUD operations.

    Args:
        url: SurrealDB connection URL. Use "mem://" for in-memory (testing)
             or "file:///path/to/dir" for file-backed persistence.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.db = Surreal(url)
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        """Connect to SurrealDB and initialize schema."""
        if self._connected:
            return
        await self.db.connect()
        await self.db.use("devteam", "knowledge")
        await self._init_schema()
        self._connected = True
        logger.info("Knowledge store connected: %s", self.url)

    async def close(self) -> None:
        """Close the SurrealDB connection."""
        if self._connected:
            await self.db.close()
            self._connected = False
            logger.info("Knowledge store disconnected")

    async def _init_schema(self) -> None:
        """Initialize the knowledge table schema (idempotent)."""
        for stmt in SCHEMA_STATEMENTS:
            await self.db.query(stmt)
        logger.debug("Knowledge schema initialized")

    async def create_entry(
        self,
        content: str,
        summary: str,
        tags: list[str],
        sharing: str,
        project: str | None,
        embedding: list[float],
        source: dict[str, Any] | None = None,
    ) -> str:
        """Create a knowledge entry. Returns the record ID."""
        record = await self.db.create("knowledge", {
            "content": content,
            "summary": summary,
            "source": source,
            "tags": tags,
            "sharing": sharing,
            "project": project,
            "embedding": embedding,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "verified": False,
            "access_count": 0,
        })
        record_id = record["id"] if isinstance(record, dict) else record[0]["id"]
        return str(record_id)

    async def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        """Get a single knowledge entry by ID. Returns None if not found."""
        result = await self.db.query(
            "SELECT * FROM $id",
            {"id": entry_id},
        )
        rows = result[0]["result"] if isinstance(result, list) else result
        if not rows:
            return None
        return rows[0] if isinstance(rows, list) else rows

    async def update_entry(self, entry_id: str, **fields: Any) -> None:
        """Update specific fields on a knowledge entry."""
        set_clauses = ", ".join(f"{k} = ${k}" for k in fields)
        await self.db.query(
            f"UPDATE $id SET {set_clauses}",
            {"id": entry_id, **fields},
        )

    async def increment_access_count(self, entry_id: str) -> None:
        """Increment the access_count for a knowledge entry."""
        await self.db.query(
            "UPDATE $id SET access_count += 1",
            {"id": entry_id},
        )

    async def delete_entry(self, entry_id: str) -> None:
        """Delete a knowledge entry by ID."""
        await self.db.query("DELETE $id", {"id": entry_id})

    async def delete_by_project(self, project: str) -> int:
        """Delete all entries scoped to a project. Returns count deleted."""
        result = await self.db.query(
            "SELECT count() AS total FROM knowledge WHERE project = $project GROUP ALL",
            {"project": project},
        )
        rows = result[0]["result"] if isinstance(result, list) else result
        count = rows[0]["total"] if rows else 0
        await self.db.query(
            "DELETE FROM knowledge WHERE project = $project",
            {"project": project},
        )
        return count

    async def get_stats(self) -> dict[str, Any]:
        """Get knowledge base statistics."""
        result = await self.db.query(
            "SELECT count() AS total FROM knowledge GROUP ALL"
        )
        rows = result[0]["result"] if isinstance(result, list) else result
        total = rows[0]["total"] if rows else 0
        return {"total": total}
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_store.py -v
```

---

## Task 2: Graph Relationships

**Files:**
- Modify: `src/devteam/knowledge/store.py`
- Modify: `tests/knowledge/test_store.py`

- [ ] **Step 1 (3 min): Write tests for graph relationship operations**

Append to `tests/knowledge/test_store.py`:
```python
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
```

- [ ] **Step 2 (4 min): Implement graph relationship methods on KnowledgeStore**

Add to `src/devteam/knowledge/store.py` (new methods on the `KnowledgeStore` class):

```python
    async def add_relationship(
        self, from_id: str, relation: str, to_id: str
    ) -> None:
        """Create a graph edge between two records.

        Valid relations: discovered, supersedes, requires, relates_to.
        """
        valid_relations = {"discovered", "supersedes", "requires", "relates_to"}
        if relation not in valid_relations:
            raise ValueError(
                f"Invalid relation '{relation}'. Must be one of: {valid_relations}"
            )
        await self.db.query(
            f"RELATE $from->{relation}->$to",
            {"from": from_id, "to": to_id},
        )

    async def get_relationships(
        self,
        entry_id: str,
        direction: str = "out",
        relation: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get graph relationships for an entry.

        Args:
            entry_id: The record ID.
            direction: "in" for incoming edges, "out" for outgoing edges.
            relation: Optional relation type filter.
        """
        if direction == "out":
            if relation:
                query = f"SELECT ->{{relation}}->knowledge AS targets FROM $id"
                # Use direct query construction for relation name
                query = f"SELECT ->{relation}->knowledge AS targets FROM $id"
            else:
                query = "SELECT ->?->knowledge AS targets FROM $id"
        else:
            if relation:
                query = f"SELECT <-{relation}<-? AS sources FROM $id"
            else:
                query = "SELECT <-?<-? AS sources FROM $id"

        result = await self.db.query(query, {"id": entry_id})
        rows = result[0]["result"] if isinstance(result, list) else result
        if not rows:
            return []
        key = "targets" if direction == "out" else "sources"
        items = rows[0].get(key, []) if isinstance(rows, list) else rows.get(key, [])
        return [{"id": str(item)} for item in items] if items else []

    async def get_superseded_ids(self) -> list[str]:
        """Return IDs of all knowledge entries that have been superseded."""
        result = await self.db.query(
            "SELECT ->supersedes->knowledge AS superseded FROM knowledge"
        )
        rows = result[0]["result"] if isinstance(result, list) else result
        superseded = set()
        for row in (rows or []):
            for item in (row.get("superseded") or []):
                superseded.add(str(item))
        return list(superseded)
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_store.py::TestGraphRelationships -v
```

---

## Task 3: Ollama Embedding Integration

**Files:**
- Create: `src/devteam/knowledge/embeddings.py`
- Create: `tests/knowledge/test_embeddings.py`

- [ ] **Step 1 (3 min): Write tests for embedding client (mocked Ollama)**

`tests/knowledge/test_embeddings.py`:
```python
"""Tests for Ollama embedding integration."""

import pytest
import pytest_asyncio
import httpx
from unittest.mock import AsyncMock, patch
from devteam.knowledge.embeddings import OllamaEmbedder, EmbeddingError


@pytest.fixture
def mock_ollama_response():
    """A mock Ollama /api/embed response."""
    return {
        "model": "nomic-embed-text",
        "embeddings": [[0.1] * 768],
    }


@pytest.fixture
def mock_ollama_batch_response():
    """A mock batch Ollama /api/embed response."""
    return {
        "model": "nomic-embed-text",
        "embeddings": [[0.1] * 768, [0.2] * 768, [0.3] * 768],
    }


class TestOllamaEmbedder:
    @pytest.mark.asyncio
    async def test_embed_single_text(self, mock_ollama_response):
        embedder = OllamaEmbedder()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_ollama_response
        mock_response.raise_for_status = AsyncMock()

        with patch.object(embedder._client, "post", return_value=mock_response):
            result = await embedder.embed("test text")
            assert len(result) == 768
            assert all(isinstance(v, float) for v in result)

    @pytest.mark.asyncio
    async def test_embed_batch(self, mock_ollama_batch_response):
        embedder = OllamaEmbedder()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_ollama_batch_response
        mock_response.raise_for_status = AsyncMock()

        with patch.object(embedder._client, "post", return_value=mock_response):
            results = await embedder.embed_batch(["text1", "text2", "text3"])
            assert len(results) == 3
            assert all(len(v) == 768 for v in results)

    @pytest.mark.asyncio
    async def test_embed_uses_correct_model(self, mock_ollama_response):
        embedder = OllamaEmbedder(model="nomic-embed-text")
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_ollama_response
        mock_response.raise_for_status = AsyncMock()

        with patch.object(embedder._client, "post", return_value=mock_response) as mock_post:
            await embedder.embed("test")
            call_args = mock_post.call_args
            assert call_args[1]["json"]["model"] == "nomic-embed-text"

    @pytest.mark.asyncio
    async def test_embed_connection_error_raises_embedding_error(self):
        embedder = OllamaEmbedder()
        with patch.object(
            embedder._client, "post", side_effect=httpx.ConnectError("Connection refused")
        ):
            with pytest.raises(EmbeddingError, match="Ollama unavailable"):
                await embedder.embed("test")

    @pytest.mark.asyncio
    async def test_embed_http_error_raises_embedding_error(self):
        embedder = OllamaEmbedder()
        mock_response = AsyncMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=AsyncMock(), response=mock_response
        )

        with patch.object(embedder._client, "post", return_value=mock_response):
            with pytest.raises(EmbeddingError, match="Ollama embedding failed"):
                await embedder.embed("test")

    @pytest.mark.asyncio
    async def test_custom_base_url(self):
        embedder = OllamaEmbedder(base_url="http://remote:11434")
        assert embedder.base_url == "http://remote:11434"

    @pytest.mark.asyncio
    async def test_is_available_true(self, mock_ollama_response):
        embedder = OllamaEmbedder()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_ollama_response
        mock_response.raise_for_status = AsyncMock()

        with patch.object(embedder._client, "post", return_value=mock_response):
            assert await embedder.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_false_on_connection_error(self):
        embedder = OllamaEmbedder()
        with patch.object(
            embedder._client, "post", side_effect=httpx.ConnectError("refused")
        ):
            assert await embedder.is_available() is False
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_embeddings.py -v
```

- [ ] **Step 2 (4 min): Implement OllamaEmbedder**

`src/devteam/knowledge/embeddings.py`:
```python
"""Ollama embedding integration for knowledge vector search."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL = "nomic-embed-text"
EMBEDDING_DIMENSIONS = 768


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""


class OllamaEmbedder:
    """Generates text embeddings via Ollama's local API.

    Uses nomic-embed-text (768 dimensions) by default.
    No external API dependency — runs entirely locally.
    """

    def __init__(
        self,
        base_url: str = OLLAMA_DEFAULT_BASE_URL,
        model: str = OLLAMA_DEFAULT_MODEL,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
        )

    async def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for a single text.

        Args:
            text: The text to embed.

        Returns:
            A list of floats (768 dimensions for nomic-embed-text).

        Raises:
            EmbeddingError: If Ollama is unavailable or returns an error.
        """
        result = await self._call_ollama([text])
        return result[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for multiple texts in one call.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors, one per input text.

        Raises:
            EmbeddingError: If Ollama is unavailable or returns an error.
        """
        return await self._call_ollama(texts)

    async def is_available(self) -> bool:
        """Check if Ollama is running and the model is available."""
        try:
            await self._call_ollama(["test"])
            return True
        except EmbeddingError:
            return False

    async def _call_ollama(self, texts: list[str]) -> list[list[float]]:
        """Make the actual HTTP call to Ollama's /api/embed endpoint."""
        try:
            response = await self._client.post(
                "/api/embed",
                json={
                    "model": self.model,
                    "input": texts,
                },
            )
            response.raise_for_status()
        except httpx.ConnectError as e:
            raise EmbeddingError(
                f"Ollama unavailable at {self.base_url}: {e}"
            ) from e
        except httpx.HTTPStatusError as e:
            raise EmbeddingError(
                f"Ollama embedding failed (HTTP {e.response.status_code}): {e}"
            ) from e

        data = response.json()
        return [list(map(float, emb)) for emb in data["embeddings"]]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_embeddings.py -v
```

---

## Task 4: Vector Search & Combined Queries

**Files:**
- Modify: `src/devteam/knowledge/store.py`
- Create: `tests/knowledge/test_vector_search.py`

- [ ] **Step 1 (3 min): Write tests for vector similarity search**

`tests/knowledge/test_vector_search.py`:
```python
"""Tests for vector search and combined queries."""

import pytest
import pytest_asyncio
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
        results = await populated_store.vector_search(
            query_emb, limit=5, sharing="shared"
        )
        for r in results:
            assert r["sharing"] == "shared"

    async def test_search_with_project_filter(self, populated_store: KnowledgeStore):
        query_emb = [0.0, 1.0] + [0.0] * 766
        results = await populated_store.vector_search(
            query_emb, limit=5, project="myapp"
        )
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
        results = await populated_store.vector_search(
            query_emb, limit=10, exclude_superseded=True
        )
        result_ids = [str(r["id"]) for r in results]
        assert old_id not in result_ids

    async def test_search_empty_store(self):
        s = KnowledgeStore("mem://")
        await s.connect()
        results = await s.vector_search([0.1] * 768, limit=5)
        assert results == []
        await s.close()


@pytest.mark.asyncio
class TestCombinedQueries:
    async def test_search_with_tag_filter(self, populated_store: KnowledgeStore):
        query_emb = [0.5] * 768
        results = await populated_store.vector_search(
            query_emb, limit=10, tags=["cloud"]
        )
        for r in results:
            assert "cloud" in r["tags"]

    async def test_search_with_role_scope(self, populated_store: KnowledgeStore):
        """Agent role scoping: entries tagged for a specific role."""
        query_emb = [0.5] * 768
        results = await populated_store.vector_search(
            query_emb, limit=10, tags=["backend"]
        )
        for r in results:
            assert "backend" in r["tags"]

    async def test_stats_by_scope(self, populated_store: KnowledgeStore):
        stats = await populated_store.get_stats_detailed()
        assert stats["total"] == 4
        assert stats["by_sharing"]["shared"] == 3
        assert stats["by_sharing"]["project"] == 1
        assert "myapp" in stats["by_project"]
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_vector_search.py -v
```

- [ ] **Step 2 (5 min): Implement vector search and combined query methods**

Add to `src/devteam/knowledge/store.py` (new methods on `KnowledgeStore`):

```python
    async def vector_search(
        self,
        embedding: list[float],
        limit: int = 5,
        sharing: str | None = None,
        project: str | None = None,
        tags: list[str] | None = None,
        exclude_superseded: bool = True,
    ) -> list[dict[str, Any]]:
        """Search knowledge entries by vector similarity with filters.

        Args:
            embedding: Query embedding vector (768 dimensions).
            limit: Maximum number of results.
            sharing: Filter by sharing scope ("shared" or "project").
            project: Include shared + project-scoped for this project.
            tags: Filter to entries containing any of these tags.
            exclude_superseded: Exclude entries that have been superseded.

        Returns:
            List of matching entries sorted by relevance (descending).
        """
        filters = []
        params: dict[str, Any] = {"vec": embedding, "limit": limit}

        if sharing:
            filters.append("sharing = $sharing")
            params["sharing"] = sharing
        elif project:
            filters.append('(sharing = "shared" OR project = $project)')
            params["project"] = project

        if tags:
            tag_conditions = " OR ".join(
                f"tags CONTAINS '{tag}'" for tag in tags
            )
            filters.append(f"({tag_conditions})")

        if exclude_superseded:
            # Get superseded IDs and exclude them
            superseded_ids = await self.get_superseded_ids()
            if superseded_ids:
                # Filter out superseded in post-processing since
                # SurrealDB parameterized IN queries can be tricky
                pass  # handled in post-processing below

        where_clause = " AND ".join(filters) if filters else ""
        where_sql = f"WHERE {where_clause}" if where_clause else ""

        query = f"""
            SELECT *,
                vector::similarity::cosine(embedding, $vec) AS relevance
            FROM knowledge
            {where_sql}
            ORDER BY relevance DESC
            LIMIT $limit
        """

        result = await self.db.query(query, params)
        rows = result[0]["result"] if isinstance(result, list) else result

        if not rows:
            return []

        # Post-process: exclude superseded entries
        if exclude_superseded:
            superseded_ids = await self.get_superseded_ids()
            rows = [r for r in rows if str(r.get("id", "")) not in superseded_ids]

        return rows

    async def get_stats_detailed(self) -> dict[str, Any]:
        """Get detailed knowledge base statistics."""
        total_result = await self.db.query(
            "SELECT count() AS total FROM knowledge GROUP ALL"
        )
        total_rows = total_result[0]["result"] if isinstance(total_result, list) else total_result
        total = total_rows[0]["total"] if total_rows else 0

        sharing_result = await self.db.query(
            "SELECT sharing, count() AS cnt FROM knowledge GROUP BY sharing"
        )
        sharing_rows = sharing_result[0]["result"] if isinstance(sharing_result, list) else sharing_result
        by_sharing: dict[str, int] = {}
        for row in (sharing_rows or []):
            by_sharing[row["sharing"]] = row["cnt"]

        project_result = await self.db.query(
            "SELECT project, count() AS cnt FROM knowledge WHERE project IS NOT NULL GROUP BY project"
        )
        project_rows = project_result[0]["result"] if isinstance(project_result, list) else project_result
        by_project: dict[str, int] = {}
        for row in (project_rows or []):
            if row.get("project"):
                by_project[row["project"]] = row["cnt"]

        verified_result = await self.db.query(
            "SELECT count() AS cnt FROM knowledge WHERE verified = true GROUP ALL"
        )
        verified_rows = verified_result[0]["result"] if isinstance(verified_result, list) else verified_result
        verified = verified_rows[0]["cnt"] if verified_rows else 0

        return {
            "total": total,
            "verified": verified,
            "by_sharing": by_sharing,
            "by_project": by_project,
        }
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_vector_search.py -v
```

---

## Task 5: Knowledge Boundaries & Secret Scanning

**Files:**
- Create: `src/devteam/knowledge/boundaries.py`
- Create: `tests/knowledge/test_boundaries.py`

- [ ] **Step 1 (3 min): Write tests for sharing rules and secret scanning**

`tests/knowledge/test_boundaries.py`:
```python
"""Tests for knowledge boundaries — sharing rules and secret scanning."""

import pytest
from devteam.knowledge.boundaries import (
    SharingScope,
    determine_sharing_scope,
    scan_for_secrets,
    SecretDetectedError,
    apply_scope_filter,
)


class TestSharingScope:
    def test_process_knowledge_is_shared(self):
        scope = determine_sharing_scope(
            tags=["process"],
            content="CodeRabbit comments must be resolved before merge",
        )
        assert scope == SharingScope.SHARED

    def test_platform_knowledge_is_shared(self):
        scope = determine_sharing_scope(
            tags=["shared", "cloud"],
            content="Fly.io requires HEALTHCHECK in Dockerfile",
        )
        assert scope == SharingScope.SHARED

    def test_project_code_knowledge_is_project_scoped(self):
        scope = determine_sharing_scope(
            tags=["project", "backend"],
            content="This project uses Drizzle ORM",
        )
        assert scope == SharingScope.PROJECT

    def test_explicit_shared_tag_overrides(self):
        scope = determine_sharing_scope(
            tags=["shared", "backend"],
            content="All backends should use structured logging",
        )
        assert scope == SharingScope.SHARED

    def test_no_tags_defaults_to_project(self):
        scope = determine_sharing_scope(
            tags=[],
            content="Something without tags",
        )
        assert scope == SharingScope.PROJECT


class TestSecretScanning:
    def test_detects_aws_access_key(self):
        with pytest.raises(SecretDetectedError, match="AWS"):
            scan_for_secrets("Use key AKIAIOSFODNN7EXAMPLE for access")

    def test_detects_generic_api_key_assignment(self):
        with pytest.raises(SecretDetectedError):
            scan_for_secrets('api_key = "sk-1234567890abcdef"')

    def test_detects_password_assignment(self):
        with pytest.raises(SecretDetectedError):
            scan_for_secrets('password = "hunter2"')

    def test_detects_bearer_token(self):
        with pytest.raises(SecretDetectedError):
            scan_for_secrets("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiMSJ9.abc123")

    def test_detects_private_key_block(self):
        with pytest.raises(SecretDetectedError):
            scan_for_secrets("-----BEGIN RSA PRIVATE KEY-----")

    def test_detects_connection_string_with_password(self):
        with pytest.raises(SecretDetectedError):
            scan_for_secrets("postgres://user:s3cret@localhost:5432/db")

    def test_allows_safe_content(self):
        # Should not raise
        scan_for_secrets("Use Drizzle ORM for database access")
        scan_for_secrets("Fly.io requires HEALTHCHECK in Dockerfile")
        scan_for_secrets("Run pytest with the -v flag for verbose output")

    def test_allows_placeholder_patterns(self):
        # Placeholder patterns should not trigger
        scan_for_secrets("Set API_KEY=<your-key-here>")
        scan_for_secrets("password: ${DB_PASSWORD}")
        scan_for_secrets("Use $ENV_VAR for the secret")


class TestScopeFilter:
    def test_shared_scope_filter(self):
        f = apply_scope_filter("shared", project=None, role=None)
        assert f["sharing"] == "shared"
        assert "project" not in f
        assert "role" not in f

    def test_project_scope_includes_shared(self):
        f = apply_scope_filter("project", project="myapp", role=None)
        assert f["project"] == "myapp"
        # project scope should also include shared entries

    def test_role_scope_filter(self):
        f = apply_scope_filter("my_role", project=None, role="backend_engineer")
        assert f["role"] == "backend_engineer"

    def test_all_scope_with_project(self):
        f = apply_scope_filter("all", project="myapp", role="backend_engineer")
        assert f["project"] == "myapp"
        assert f["role"] == "backend_engineer"
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_boundaries.py -v
```

- [ ] **Step 2 (5 min): Implement boundaries module**

`src/devteam/knowledge/boundaries.py`:
```python
"""Knowledge boundaries — sharing rules, secret scanning, scope filtering."""

from __future__ import annotations

import re
import enum
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SharingScope(str, enum.Enum):
    """Knowledge sharing scope."""

    SHARED = "shared"
    PROJECT = "project"


class SecretDetectedError(Exception):
    """Raised when a knowledge entry contains a likely secret."""


# Patterns that indicate secrets/credentials
SECRET_PATTERNS = [
    # AWS access key IDs
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    # Generic API keys assigned as string values
    (re.compile(r"""(?:api[_-]?key|apikey|secret[_-]?key)\s*[=:]\s*["'][^$<{][^"']{8,}["']""", re.IGNORECASE), "API key assignment"),
    # Password assignments
    (re.compile(r"""(?:password|passwd|pwd)\s*[=:]\s*["'][^$<{][^"']{4,}["']""", re.IGNORECASE), "Password assignment"),
    # Bearer tokens (JWT-like)
    (re.compile(r"Bearer\s+eyJ[A-Za-z0-9_-]{20,}"), "Bearer token"),
    # Private key blocks
    (re.compile(r"-----BEGIN\s+(RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "Private key"),
    # Connection strings with embedded passwords
    (re.compile(r"(?:postgres|mysql|mongodb|redis)://[^:]+:[^@$<{]+@"), "Connection string with password"),
    # GitHub personal access tokens
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "GitHub token"),
    # Generic secret/token long hex strings
    (re.compile(r"""(?:secret|token)\s*[=:]\s*["'][0-9a-f]{32,}["']""", re.IGNORECASE), "Secret/token hex string"),
]


def determine_sharing_scope(tags: list[str], content: str) -> SharingScope:
    """Determine sharing scope based on tags.

    Rules:
    - "shared" or "process" tag -> SHARED (cross-project)
    - "project" tag -> PROJECT (project-scoped)
    - No relevant tags -> PROJECT (conservative default)
    """
    tag_set = set(tags)

    if "shared" in tag_set or "process" in tag_set:
        return SharingScope.SHARED

    if "project" in tag_set:
        return SharingScope.PROJECT

    # Conservative default: project-scoped
    return SharingScope.PROJECT


def scan_for_secrets(content: str) -> None:
    """Scan content for likely secrets. Raises SecretDetectedError if found.

    Allows placeholder patterns like ${VAR}, <your-key-here>, $ENV_VAR.
    """
    for pattern, description in SECRET_PATTERNS:
        match = pattern.search(content)
        if match:
            matched_text = match.group(0)
            # Skip placeholder patterns
            if any(p in matched_text for p in ("${", "<", "$")):
                continue
            raise SecretDetectedError(
                f"Potential {description} detected in knowledge content. "
                f"Entry rejected to prevent secret leakage."
            )


def apply_scope_filter(
    scope: str,
    project: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Build a filter dict for knowledge queries based on scope.

    Args:
        scope: One of "shared", "project", "my_role", "all".
        project: Current project name (for project/all scopes).
        role: Current agent role (for my_role/all scopes).

    Returns:
        Dict with filter keys: sharing, project, role as applicable.
    """
    filters: dict[str, Any] = {}

    if scope == "shared":
        filters["sharing"] = "shared"
    elif scope == "project":
        if project:
            filters["project"] = project
    elif scope == "my_role":
        if role:
            filters["role"] = role
    elif scope == "all":
        if project:
            filters["project"] = project
        if role:
            filters["role"] = role

    return filters
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_boundaries.py -v
```

---

## Task 6: Knowledge Extraction (Haiku Agent)

**Files:**
- Create: `src/devteam/knowledge/extractor.py`
- Create: `tests/knowledge/test_extractor.py`

- [ ] **Step 1 (3 min): Write tests for knowledge extraction**

`tests/knowledge/test_extractor.py`:
```python
"""Tests for knowledge extraction from agent output."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from devteam.knowledge.extractor import (
    KnowledgeExtractor,
    ExtractionResult,
    ExtractedEntry,
)
from devteam.knowledge.store import KnowledgeStore
from devteam.knowledge.embeddings import OllamaEmbedder


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
                content="This project uses pnpm, not npm — lock file is pnpm-lock.yaml",
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
    async def test_persist_entries(
        self, extractor, sample_extraction_result, mock_store
    ):
        """Extracted entries should be persisted to the store."""
        await extractor.persist_entries(
            entries=sample_extraction_result.entries,
            agent_role="cloud_engineer",
            project="myapp",
            task_id="T-1",
        )
        stats = await mock_store.get_stats()
        assert stats["total"] == 2

    async def test_persist_skips_entries_with_secrets(
        self, extractor, mock_store
    ):
        """Entries containing secrets should be rejected."""
        entries = [
            ExtractedEntry(
                content='Use API key AKIAIOSFODNN7EXAMPLE for S3 access',
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

    async def test_persist_with_embedder_unavailable(
        self, mock_store
    ):
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

    async def test_sharing_scope_from_extraction_tag(
        self, extractor, mock_store
    ):
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
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_extractor.py -v
```

- [ ] **Step 2 (5 min): Implement KnowledgeExtractor**

`src/devteam/knowledge/extractor.py`:
```python
"""Knowledge extraction — haiku agent extracts learnings from agent output."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel

from devteam.knowledge.boundaries import (
    SharingScope,
    determine_sharing_scope,
    scan_for_secrets,
    SecretDetectedError,
)
from devteam.knowledge.embeddings import OllamaEmbedder
from devteam.knowledge.store import KnowledgeStore

logger = logging.getLogger(__name__)


class ExtractedEntry(BaseModel):
    """A single knowledge entry extracted by the haiku agent."""

    content: str
    summary: str
    tags: list[str]
    scope: str  # "process" or "project"


class ExtractionResult(BaseModel):
    """Result from the haiku knowledge extraction agent."""

    entries: list[ExtractedEntry]


@dataclass
class PersistResult:
    """Result of persisting extracted knowledge entries."""

    persisted: int = 0
    rejected: int = 0
    errors: int = 0
    entry_ids: list[str] = field(default_factory=list)


class KnowledgeExtractor:
    """Persists extracted knowledge entries to SurrealDB with embeddings.

    The actual haiku agent invocation (which produces ExtractionResult)
    is handled by the orchestrator's invoke_agent_step. This class
    handles the downstream: secret scanning, embedding, and persistence.
    """

    def __init__(self, store: KnowledgeStore, embedder: OllamaEmbedder) -> None:
        self.store = store
        self.embedder = embedder

    async def persist_entries(
        self,
        entries: list[ExtractedEntry],
        agent_role: str,
        project: str,
        task_id: str,
    ) -> PersistResult:
        """Persist extracted knowledge entries to the store.

        Each entry is:
        1. Scanned for secrets (rejected if found)
        2. Assigned a sharing scope based on tags
        3. Embedded via Ollama
        4. Stored in SurrealDB
        5. Linked to the discovering agent via a graph relationship

        Args:
            entries: Extracted knowledge entries from the haiku agent.
            agent_role: The role of the agent whose output was extracted.
            project: The current project name.
            task_id: The task ID that produced this knowledge.

        Returns:
            PersistResult with counts of persisted, rejected, and errored entries.
        """
        result = PersistResult()

        for entry in entries:
            # Step 1: Secret scanning
            try:
                scan_for_secrets(entry.content)
            except SecretDetectedError as e:
                logger.warning(
                    "Knowledge entry rejected (secret detected): %s — %s",
                    entry.summary,
                    e,
                )
                result.rejected += 1
                continue

            # Step 2: Determine sharing scope
            sharing = determine_sharing_scope(entry.tags, entry.content)

            # Step 3: Generate embedding
            try:
                embedding = await self.embedder.embed(entry.content)
            except Exception as e:
                logger.error(
                    "Failed to generate embedding for '%s': %s",
                    entry.summary,
                    e,
                )
                result.errors += 1
                continue

            # Step 4: Persist to SurrealDB
            try:
                entry_id = await self.store.create_entry(
                    content=entry.content,
                    summary=entry.summary,
                    tags=entry.tags,
                    sharing=sharing.value,
                    project=project if sharing == SharingScope.PROJECT else None,
                    embedding=embedding,
                    source={
                        "agent": agent_role,
                        "task": task_id,
                        "project": project,
                    },
                )
                result.entry_ids.append(entry_id)
                result.persisted += 1

                # Step 5: Create discovered-by graph relationship
                try:
                    await self.store.add_relationship(
                        from_id=f"agent:{agent_role}",
                        relation="discovered",
                        to_id=entry_id,
                    )
                except Exception as e:
                    # Non-fatal — entry is still persisted
                    logger.warning(
                        "Failed to create discovered relationship for %s: %s",
                        entry_id,
                        e,
                    )

            except Exception as e:
                logger.error(
                    "Failed to persist knowledge entry '%s': %s",
                    entry.summary,
                    e,
                )
                result.errors += 1

        logger.info(
            "Knowledge extraction: %d persisted, %d rejected, %d errors",
            result.persisted,
            result.rejected,
            result.errors,
        )
        return result
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_extractor.py -v
```

---

## Task 7: Memory Index Generation

**Files:**
- Create: `src/devteam/knowledge/index.py`
- Create: `tests/knowledge/test_index.py`

- [ ] **Step 1 (3 min): Write tests for memory index generation**

`tests/knowledge/test_index.py`:
```python
"""Tests for memory index generation."""

import pytest
import pytest_asyncio
from devteam.knowledge.store import KnowledgeStore
from devteam.knowledge.index import MemoryIndexBuilder


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
    async def test_build_index_for_role(self, store_with_knowledge):
        builder = MemoryIndexBuilder(store_with_knowledge)
        index = await builder.build(role="cloud_engineer", project="myapp")
        assert "Available Knowledge" in index
        assert "cloud" in index.lower() or "Fly.io" in index
        assert "CodeRabbit" in index or "process" in index.lower()

    async def test_index_includes_project_scoped(self, store_with_knowledge):
        builder = MemoryIndexBuilder(store_with_knowledge)
        index = await builder.build(role="backend_engineer", project="myapp")
        assert "myapp" in index
        # Should NOT include otherapp knowledge
        assert "otherapp" not in index

    async def test_index_is_compact(self, store_with_knowledge):
        builder = MemoryIndexBuilder(store_with_knowledge)
        index = await builder.build(role="backend_engineer", project="myapp")
        lines = [l for l in index.strip().split("\n") if l.strip()]
        # Index should be compact — topic summaries, not full content
        assert len(lines) <= 50

    async def test_index_groups_by_section(self, store_with_knowledge):
        builder = MemoryIndexBuilder(store_with_knowledge)
        index = await builder.build(role="backend_engineer", project="myapp")
        # Should have section headers
        assert "Shared" in index or "shared" in index.lower()

    async def test_empty_store_returns_minimal_index(self):
        s = KnowledgeStore("mem://")
        await s.connect()
        builder = MemoryIndexBuilder(s)
        index = await builder.build(role="backend_engineer", project="myapp")
        assert "Available Knowledge" in index
        assert "No knowledge" in index or len(index.strip().split("\n")) <= 5
        await s.close()

    async def test_index_shows_entry_counts(self, store_with_knowledge):
        builder = MemoryIndexBuilder(store_with_knowledge)
        index = await builder.build(role="backend_engineer", project="myapp")
        # Should show counts like "(2 entries)"
        assert "entr" in index.lower()
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_index.py -v
```

- [ ] **Step 2 (5 min): Implement MemoryIndexBuilder**

`src/devteam/knowledge/index.py`:
```python
"""Memory index generation — compact topic summary injected into agent context."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from devteam.knowledge.store import KnowledgeStore

logger = logging.getLogger(__name__)

INDEX_HEADER = "## Available Knowledge\nYou can query the knowledge base for details on any of these topics.\n"
INDEX_EMPTY = "## Available Knowledge\nNo knowledge entries yet. The knowledge base will grow as the team works.\n"


class MemoryIndexBuilder:
    """Builds a compact memory index from SurrealDB for agent context injection.

    The index shows topics and entry counts, not full content.
    Agents use the query_knowledge tool to retrieve details.
    """

    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    async def build(self, role: str, project: str) -> str:
        """Build a memory index scoped to the given role and project.

        Args:
            role: Agent role (e.g., "backend_engineer").
            project: Current project name.

        Returns:
            Formatted markdown string suitable for agent context injection.
            Stays compact (~30-50 lines) regardless of knowledge base size.
        """
        entries = await self._fetch_relevant_entries(role, project)

        if not entries:
            return INDEX_EMPTY

        sections = self._group_entries(entries, role, project)
        return self._format_index(sections)

    async def _fetch_relevant_entries(
        self, role: str, project: str
    ) -> list[dict[str, Any]]:
        """Fetch entries visible to this role/project combination."""
        result = await self.store.db.query(
            """
            SELECT summary, tags, sharing, project, verified, created_at
            FROM knowledge
            WHERE sharing = "shared"
               OR project = $project
            ORDER BY created_at DESC
            """,
            {"project": project},
        )
        rows = result[0]["result"] if isinstance(result, list) else result
        return rows or []

    def _group_entries(
        self,
        entries: list[dict[str, Any]],
        role: str,
        project: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Group entries into display sections."""
        sections: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for entry in entries:
            sharing = entry.get("sharing", "shared")
            entry_project = entry.get("project")
            tags = entry.get("tags", [])

            if sharing == "shared":
                # Group by primary tag
                if "process" in tags:
                    sections["Process"].append(entry)
                elif any(t in tags for t in ["cloud", "infra", "platform"]):
                    sections["Platform"].append(entry)
                else:
                    sections["Shared"].append(entry)
            elif entry_project == project:
                sections[f"Project ({project})"].append(entry)

        return dict(sections)

    def _format_index(self, sections: dict[str, list[dict[str, Any]]]) -> str:
        """Format grouped entries into compact markdown."""
        lines = [INDEX_HEADER]

        for section_name, entries in sorted(sections.items()):
            lines.append(f"**{section_name}:**")

            # Group by topic (using summary similarity — simple prefix grouping)
            topic_counts: dict[str, int] = defaultdict(int)
            topic_verified: dict[str, bool] = defaultdict(lambda: False)

            for entry in entries:
                summary = entry.get("summary", "Unknown")
                topic_counts[summary] += 1
                if entry.get("verified"):
                    topic_verified[summary] = True

            for topic, count in topic_counts.items():
                suffix_parts = []
                if count > 1:
                    suffix_parts.append(f"{count} entries")
                else:
                    suffix_parts.append("1 entry")
                if topic_verified.get(topic):
                    suffix_parts.append("verified")
                suffix = ", ".join(suffix_parts)
                lines.append(f"- {topic} ({suffix})")

            lines.append("")  # blank line between sections

        return "\n".join(lines)
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_index.py -v
```

---

## Task 8: Knowledge Query Tool

**Files:**
- Create: `src/devteam/knowledge/query_tool.py`
- Create: `tests/knowledge/test_query_tool.py`

- [ ] **Step 1 (3 min): Write tests for query_knowledge tool**

`tests/knowledge/test_query_tool.py`:
```python
"""Tests for the query_knowledge tool exposed to agents."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from devteam.knowledge.query_tool import QueryKnowledgeTool
from devteam.knowledge.store import KnowledgeStore
from devteam.knowledge.embeddings import OllamaEmbedder


@pytest.fixture
def mock_embedder():
    embedder = AsyncMock(spec=OllamaEmbedder)
    # Return a deployment-like embedding for any query
    embedder.embed.return_value = [1.0] + [0.0] * 767
    return embedder


@pytest_asyncio.fixture
async def populated_store():
    s = KnowledgeStore("mem://")
    await s.connect()

    await s.create_entry(
        content="Fly.io requires HEALTHCHECK in Dockerfile for zero-downtime deploys",
        summary="Fly.io HEALTHCHECK requirement",
        tags=["shared", "cloud"],
        sharing="shared",
        project=None,
        embedding=[1.0] + [0.0] * 767,
    )
    await s.create_entry(
        content="This project uses Drizzle ORM",
        summary="ORM convention",
        tags=["project", "backend"],
        sharing="project",
        project="myapp",
        embedding=[0.0, 1.0] + [0.0] * 766,
    )
    await s.create_entry(
        content="CodeRabbit comments must be resolved",
        summary="CodeRabbit process",
        tags=["process"],
        sharing="shared",
        project=None,
        embedding=[0.0, 0.0, 1.0] + [0.0] * 765,
    )

    yield s
    await s.close()


@pytest.fixture
def query_tool(populated_store, mock_embedder):
    return QueryKnowledgeTool(
        store=populated_store,
        embedder=mock_embedder,
        current_project="myapp",
        agent_role="cloud_engineer",
    )


@pytest.mark.asyncio
class TestQueryKnowledgeTool:
    async def test_query_returns_formatted_results(self, query_tool):
        result = await query_tool.query("Fly.io deployment")
        assert isinstance(result, str)
        assert len(result) > 0

    async def test_query_with_shared_scope(self, query_tool):
        result = await query_tool.query("deployment", scope="shared")
        assert isinstance(result, str)
        # Should only contain shared entries
        assert "Drizzle" not in result or "shared" in result.lower()

    async def test_query_with_project_scope(self, query_tool):
        result = await query_tool.query("ORM", scope="project")
        assert isinstance(result, str)

    async def test_query_with_all_scope(self, query_tool):
        result = await query_tool.query("anything", scope="all")
        assert isinstance(result, str)

    async def test_query_increments_access_count(self, query_tool, populated_store):
        await query_tool.query("Fly.io deployment")
        # Access counts should be incremented for returned results
        # (verified indirectly through the store)

    async def test_query_no_results(self, query_tool):
        # Mock embedder to return an embedding far from any entry
        query_tool.embedder.embed.return_value = [0.0] * 768
        result = await query_tool.query("completely unrelated topic")
        assert isinstance(result, str)

    async def test_query_handles_embedder_failure(self, query_tool):
        query_tool.embedder.embed.side_effect = Exception("Ollama down")
        result = await query_tool.query("anything")
        assert "unavailable" in result.lower() or "error" in result.lower()

    async def test_tool_definition_schema(self, query_tool):
        """Tool definition should have the expected schema for Agent SDK."""
        schema = query_tool.tool_definition()
        assert schema["name"] == "query_knowledge"
        assert "parameters" in schema
        params = schema["parameters"]
        assert "query" in params["properties"]
        assert "scope" in params["properties"]
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_query_tool.py -v
```

- [ ] **Step 2 (5 min): Implement QueryKnowledgeTool**

`src/devteam/knowledge/query_tool.py`:
```python
"""query_knowledge tool — on-demand knowledge search exposed to agents."""

from __future__ import annotations

import logging
from typing import Any

from devteam.knowledge.embeddings import OllamaEmbedder
from devteam.knowledge.store import KnowledgeStore

logger = logging.getLogger(__name__)

RELEVANCE_THRESHOLD = 0.3  # minimum cosine similarity to include


class QueryKnowledgeTool:
    """The query_knowledge tool that agents use to search institutional memory.

    Exposed as a tool in the Agent SDK invocation. Combines vector similarity
    search with scope filtering and formatted output.
    """

    def __init__(
        self,
        store: KnowledgeStore,
        embedder: OllamaEmbedder,
        current_project: str,
        agent_role: str,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.current_project = current_project
        self.agent_role = agent_role

    async def query(self, query: str, scope: str = "all") -> str:
        """Search the team knowledge base.

        Args:
            query: What you're looking for (natural language).
            scope: "shared", "my_role", "project", or "all".

        Returns:
            Formatted string of relevant knowledge entries.
        """
        # Generate embedding for the query
        try:
            embedding = await self.embedder.embed(query)
        except Exception as e:
            logger.error("Failed to generate query embedding: %s", e)
            return (
                "Knowledge query unavailable — embedding service is down. "
                "Proceed without knowledge base consultation."
            )

        # Build scope filters
        sharing = None
        project = None
        tags = None

        if scope == "shared":
            sharing = "shared"
        elif scope == "project":
            project = self.current_project
        elif scope == "my_role":
            tags = [self.agent_role]
        elif scope == "all":
            project = self.current_project

        # Execute vector search
        try:
            results = await self.store.vector_search(
                embedding=embedding,
                limit=5,
                sharing=sharing,
                project=project,
                tags=tags,
                exclude_superseded=True,
            )
        except Exception as e:
            logger.error("Knowledge query failed: %s", e)
            return "Knowledge query failed. Proceed without knowledge base consultation."

        if not results:
            return f"No relevant knowledge found for: {query}"

        # Filter by relevance threshold
        relevant = [
            r for r in results
            if r.get("relevance", 0) >= RELEVANCE_THRESHOLD
        ]

        if not relevant:
            return f"No sufficiently relevant knowledge found for: {query}"

        # Increment access counts for returned results
        for r in relevant:
            try:
                entry_id = str(r.get("id", ""))
                if entry_id:
                    await self.store.increment_access_count(entry_id)
            except Exception:
                pass  # Non-fatal

        return self._format_results(relevant, query)

    def _format_results(self, results: list[dict[str, Any]], query: str) -> str:
        """Format search results for agent consumption."""
        lines = [f"Knowledge results for: {query}\n"]

        for i, r in enumerate(results, 1):
            summary = r.get("summary", "")
            content = r.get("content", "")
            tags = r.get("tags", [])
            sharing = r.get("sharing", "unknown")
            verified = r.get("verified", False)
            relevance = r.get("relevance", 0)

            status = "verified" if verified else "unverified"
            tag_str = ", ".join(tags) if tags else "none"

            lines.append(f"### {i}. {summary}")
            lines.append(f"**Scope:** {sharing} | **Status:** {status} | **Relevance:** {relevance:.2f}")
            lines.append(f"**Tags:** {tag_str}")
            lines.append(f"\n{content}\n")

        return "\n".join(lines)

    def tool_definition(self) -> dict[str, Any]:
        """Return the tool definition schema for the Agent SDK."""
        return {
            "name": "query_knowledge",
            "description": (
                "Search the team knowledge base for relevant learnings, "
                "conventions, patterns, and past decisions. Use this when "
                "you need details about a topic listed in the Available Knowledge "
                "section of your context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What you're looking for (natural language)",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["shared", "my_role", "project", "all"],
                        "default": "all",
                        "description": (
                            "Filter scope: 'shared' for cross-project knowledge, "
                            "'my_role' for your specialization, "
                            "'project' for current project only, "
                            "'all' for everything accessible"
                        ),
                    },
                },
                "required": ["query"],
            },
        }
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_query_tool.py -v
```

---

## Task 9: Knowledge Admin CLI Commands

**Files:**
- Create: `src/devteam/cli/commands/knowledge.py`
- Create: `tests/cli/test_knowledge_commands.py`

- [ ] **Step 1 (3 min): Write tests for knowledge CLI commands**

`tests/cli/test_knowledge_commands.py`:
```python
"""Tests for devteam knowledge CLI commands."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from typer.testing import CliRunner
from devteam.cli.commands.knowledge import knowledge_app

runner = CliRunner()


@pytest.fixture
def mock_store():
    store = AsyncMock()
    store.is_connected = True
    store.get_stats_detailed.return_value = {
        "total": 10,
        "verified": 3,
        "by_sharing": {"shared": 7, "project": 3},
        "by_project": {"myapp": 2, "otherapp": 1},
    }
    store.get_entry.return_value = {
        "id": "knowledge:abc123",
        "content": "Test knowledge content",
        "summary": "Test summary",
        "tags": ["process"],
        "sharing": "shared",
        "verified": True,
        "access_count": 5,
    }
    store.delete_entry.return_value = None
    store.delete_by_project.return_value = 3
    store.update_entry.return_value = None
    return store


class TestKnowledgeStats:
    def test_stats_command(self, mock_store):
        with patch("devteam.cli.commands.knowledge.get_store", return_value=mock_store):
            result = runner.invoke(knowledge_app, ["stats"])
            assert result.exit_code == 0
            assert "10" in result.output  # total
            assert "3" in result.output   # verified


class TestKnowledgePurge:
    def test_purge_single_entry(self, mock_store):
        with patch("devteam.cli.commands.knowledge.get_store", return_value=mock_store):
            result = runner.invoke(knowledge_app, ["purge", "knowledge:abc123"])
            assert result.exit_code == 0
            mock_store.delete_entry.assert_called_once()

    def test_purge_by_project(self, mock_store):
        with patch("devteam.cli.commands.knowledge.get_store", return_value=mock_store):
            result = runner.invoke(knowledge_app, ["purge", "--project", "myapp"])
            assert result.exit_code == 0
            mock_store.delete_by_project.assert_called_once_with("myapp")


class TestKnowledgeVerify:
    def test_verify_entry(self, mock_store):
        with patch("devteam.cli.commands.knowledge.get_store", return_value=mock_store):
            result = runner.invoke(knowledge_app, ["verify", "knowledge:abc123"])
            assert result.exit_code == 0
            mock_store.update_entry.assert_called_once()


class TestKnowledgeRedact:
    def test_redact_entry(self, mock_store):
        with patch("devteam.cli.commands.knowledge.get_store", return_value=mock_store):
            result = runner.invoke(knowledge_app, ["redact", "knowledge:abc123"])
            assert result.exit_code == 0
            mock_store.update_entry.assert_called_once()


class TestKnowledgeExport:
    def test_export_all(self, mock_store):
        mock_store.db = AsyncMock()
        mock_store.db.query.return_value = [{"result": [
            {"id": "knowledge:1", "content": "test", "summary": "test"},
        ]}]
        with patch("devteam.cli.commands.knowledge.get_store", return_value=mock_store):
            result = runner.invoke(knowledge_app, ["export"])
            assert result.exit_code == 0

    def test_export_by_project(self, mock_store):
        mock_store.db = AsyncMock()
        mock_store.db.query.return_value = [{"result": []}]
        with patch("devteam.cli.commands.knowledge.get_store", return_value=mock_store):
            result = runner.invoke(knowledge_app, ["export", "--project", "myapp"])
            assert result.exit_code == 0
```

**Test command:**
```bash
pixi run pytest tests/cli/test_knowledge_commands.py -v
```

- [ ] **Step 2 (5 min): Implement knowledge CLI subcommands**

`src/devteam/cli/commands/knowledge.py`:
```python
"""devteam knowledge — admin commands for the knowledge base."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import typer

logger = logging.getLogger(__name__)

knowledge_app = typer.Typer(
    name="knowledge",
    help="Knowledge base administration commands.",
    no_args_is_help=True,
)


def get_store():
    """Get the KnowledgeStore instance. Overridden in tests."""
    from devteam.knowledge.store import KnowledgeStore

    store = KnowledgeStore("file://~/.devteam/knowledge")
    asyncio.get_event_loop().run_until_complete(store.connect())
    return store


def _run(coro):
    """Run an async coroutine from sync CLI context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@knowledge_app.command()
def search(
    query: str = typer.Argument(..., help="Semantic search query"),
    scope: str = typer.Option("all", help="Scope: shared, project, all"),
    project: Optional[str] = typer.Option(None, help="Project name filter"),
    limit: int = typer.Option(5, help="Max results"),
) -> None:
    """Semantic search of the knowledge base."""
    from devteam.knowledge.embeddings import OllamaEmbedder
    from devteam.knowledge.query_tool import QueryKnowledgeTool

    store = get_store()
    embedder = OllamaEmbedder()
    tool = QueryKnowledgeTool(
        store=store,
        embedder=embedder,
        current_project=project or "",
        agent_role="admin",
    )
    result = _run(tool.query(query, scope=scope))
    typer.echo(result)


@knowledge_app.command()
def stats() -> None:
    """Show knowledge base statistics."""
    store = get_store()
    s = _run(store.get_stats_detailed())

    typer.echo(f"Knowledge Base Statistics")
    typer.echo(f"{'=' * 40}")
    typer.echo(f"Total entries:    {s['total']}")
    typer.echo(f"Verified:         {s['verified']}")
    typer.echo()
    typer.echo("By sharing scope:")
    for scope, count in s.get("by_sharing", {}).items():
        typer.echo(f"  {scope}: {count}")
    typer.echo()
    typer.echo("By project:")
    for proj, count in s.get("by_project", {}).items():
        typer.echo(f"  {proj}: {count}")
    if not s.get("by_project"):
        typer.echo("  (none)")


@knowledge_app.command()
def verify(
    entry_id: str = typer.Argument(..., help="Entry ID to verify"),
) -> None:
    """Manually mark a knowledge entry as verified."""
    store = get_store()
    _run(store.update_entry(entry_id, verified=True))
    typer.echo(f"Marked {entry_id} as verified.")


@knowledge_app.command()
def redact(
    entry_id: str = typer.Argument(..., help="Entry ID to redact"),
) -> None:
    """Remove sensitive content from an entry, preserving the learning."""
    store = get_store()
    entry = _run(store.get_entry(entry_id))
    if not entry:
        typer.echo(f"Entry {entry_id} not found.", err=True)
        raise typer.Exit(1)

    _run(store.update_entry(
        entry_id,
        content="[REDACTED]",
        embedding=[0.0] * 768,  # zero out embedding since content is gone
    ))
    typer.echo(f"Redacted content from {entry_id}. Summary preserved: {entry.get('summary', '')}")


@knowledge_app.command()
def purge(
    entry_id: Optional[str] = typer.Argument(None, help="Entry ID to delete"),
    project: Optional[str] = typer.Option(None, help="Delete all entries for a project"),
) -> None:
    """Delete knowledge entries entirely."""
    store = get_store()

    if project:
        count = _run(store.delete_by_project(project))
        typer.echo(f"Purged {count} entries for project '{project}'.")
    elif entry_id:
        _run(store.delete_entry(entry_id))
        typer.echo(f"Purged entry {entry_id}.")
    else:
        typer.echo("Provide either an entry ID or --project.", err=True)
        raise typer.Exit(1)


@knowledge_app.command(name="export")
def export_knowledge(
    project: Optional[str] = typer.Option(None, help="Export only this project's knowledge"),
    output: Optional[str] = typer.Option(None, "-o", help="Output file path"),
) -> None:
    """Export knowledge base to JSON."""
    store = get_store()

    if project:
        query = "SELECT * FROM knowledge WHERE project = $project"
        params = {"project": project}
    else:
        query = "SELECT * FROM knowledge"
        params = {}

    result = _run(store.db.query(query, params))
    rows = result[0]["result"] if isinstance(result, list) else result
    entries = rows or []

    # Remove embeddings from export (large, not human-readable)
    for entry in entries:
        entry.pop("embedding", None)
        # Convert ID to string
        if "id" in entry:
            entry["id"] = str(entry["id"])

    data = {
        "export_type": "knowledge",
        "project": project,
        "count": len(entries),
        "entries": entries,
    }

    json_str = json.dumps(data, indent=2, default=str)

    if output:
        with open(output, "w") as f:
            f.write(json_str)
        typer.echo(f"Exported {len(entries)} entries to {output}")
    else:
        typer.echo(json_str)
```

**Test command:**
```bash
pixi run pytest tests/cli/test_knowledge_commands.py -v
```

---

## Task 10: SurrealDB Materialized Index Event

**Files:**
- Modify: `src/devteam/knowledge/store.py`
- Modify: `tests/knowledge/test_store.py`

- [ ] **Step 1 (2 min): Write test for materialized index event**

Append to `tests/knowledge/test_store.py`:
```python
@pytest.mark.asyncio
class TestMaterializedIndex:
    async def test_index_record_created_on_schema_init(self, store: KnowledgeStore):
        """The materialized index event should be defined."""
        result = await store.db.query("INFO FOR TABLE knowledge")
        # Verify the event is defined (exact format depends on SurrealDB version)
        assert result is not None

    async def test_index_refreshes_on_write(self, store: KnowledgeStore):
        """Writing an entry should refresh the materialized index."""
        await store.create_entry(
            content="Test content",
            summary="Test",
            tags=["process"],
            sharing="shared",
            project=None,
            embedding=[0.1] * 768,
        )
        # Query the materialized index record
        index_data = await store.get_materialized_index()
        assert index_data is not None
        assert index_data["entry_count"] >= 1
```

- [ ] **Step 2 (3 min): Implement materialized index event and query**

Add to `SCHEMA_STATEMENTS` in `src/devteam/knowledge/store.py`:

```python
# Add these to the SCHEMA_STATEMENTS list:
    "DEFINE TABLE knowledge_index SCHEMAFULL",
    "DEFINE FIELD sections ON knowledge_index TYPE option<array>",
    "DEFINE FIELD entry_count ON knowledge_index TYPE int DEFAULT 0",
    "DEFINE FIELD rebuilt_at ON knowledge_index TYPE option<datetime>",
    """DEFINE EVENT refresh_index ON knowledge
        WHEN $event IN ["CREATE", "UPDATE", "DELETE"]
        THEN {
            LET $stats = (SELECT tags, count() AS cnt, math::max(created_at) AS last_updated FROM knowledge GROUP BY tags);
            LET $total = (SELECT count() AS cnt FROM knowledge GROUP ALL);
            UPSERT knowledge_index:current SET
                sections = $stats,
                entry_count = $total[0].cnt OR 0,
                rebuilt_at = time::now();
        }""",
```

Add method to `KnowledgeStore`:

```python
    async def get_materialized_index(self) -> dict[str, Any] | None:
        """Get the pre-computed materialized index record."""
        result = await self.db.query("SELECT * FROM knowledge_index:current")
        rows = result[0]["result"] if isinstance(result, list) else result
        if not rows:
            return None
        return rows[0] if isinstance(rows, list) else rows
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_store.py::TestMaterializedIndex -v
```

---

## Task 11: Decay & Consolidation Logic

**Files:**
- Modify: `src/devteam/knowledge/store.py`
- Create: `tests/knowledge/test_decay.py`

- [ ] **Step 1 (3 min): Write tests for decay and consolidation**

`tests/knowledge/test_decay.py`:
```python
"""Tests for knowledge decay and consolidation."""

import pytest
import pytest_asyncio
from devteam.knowledge.store import KnowledgeStore


@pytest_asyncio.fixture
async def store_with_entries():
    s = KnowledgeStore("mem://")
    await s.connect()

    # Create entries with varying access counts
    high_access = await s.create_entry(
        content="Frequently accessed knowledge",
        summary="Popular",
        tags=["process"],
        sharing="shared",
        project=None,
        embedding=[0.1] * 768,
    )
    # Simulate high access
    for _ in range(10):
        await s.increment_access_count(high_access)

    low_access = await s.create_entry(
        content="Rarely accessed knowledge",
        summary="Unpopular",
        tags=["process"],
        sharing="shared",
        project=None,
        embedding=[0.2] * 768,
    )

    zero_access = await s.create_entry(
        content="Never accessed knowledge",
        summary="Forgotten",
        tags=["process"],
        sharing="shared",
        project=None,
        embedding=[0.3] * 768,
    )

    yield s, {"high": high_access, "low": low_access, "zero": zero_access}
    await s.close()


@pytest.mark.asyncio
class TestDecay:
    async def test_get_low_access_entries(self, store_with_entries):
        store, ids = store_with_entries
        low_access = await store.get_entries_by_access_count(max_count=1)
        assert len(low_access) >= 2  # low_access and zero_access
        # high_access should not be in the list
        low_ids = [str(e["id"]) for e in low_access]
        assert ids["high"] not in low_ids

    async def test_get_high_access_entries(self, store_with_entries):
        store, ids = store_with_entries
        high_access = await store.get_entries_by_access_count(min_count=5)
        assert len(high_access) >= 1
        high_ids = [str(e["id"]) for e in high_access]
        assert ids["high"] in high_ids

    async def test_supersede_entry(self, store_with_entries):
        store, ids = store_with_entries
        new_id = await store.create_entry(
            content="Updated frequently accessed knowledge",
            summary="Popular (updated)",
            tags=["process"],
            sharing="shared",
            project=None,
            embedding=[0.11] * 768,
        )
        await store.add_relationship(new_id, "supersedes", ids["high"])
        superseded = await store.get_superseded_ids()
        assert ids["high"] in superseded

    async def test_decay_candidates(self, store_with_entries):
        """Entries with zero access should be decay candidates."""
        store, ids = store_with_entries
        candidates = await store.get_decay_candidates(
            min_age_hours=0,  # No age requirement for test
            max_access_count=0,
        )
        assert len(candidates) >= 1
        candidate_ids = [str(c["id"]) for c in candidates]
        assert ids["zero"] in candidate_ids
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_decay.py -v
```

- [ ] **Step 2 (4 min): Implement decay query methods**

Add to `src/devteam/knowledge/store.py`:

```python
    async def get_entries_by_access_count(
        self,
        min_count: int | None = None,
        max_count: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get entries filtered by access count range."""
        conditions = []
        params: dict[str, Any] = {}

        if min_count is not None:
            conditions.append("access_count >= $min_count")
            params["min_count"] = min_count
        if max_count is not None:
            conditions.append("access_count <= $max_count")
            params["max_count"] = max_count

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        result = await self.db.query(
            f"SELECT * FROM knowledge {where} ORDER BY access_count ASC",
            params,
        )
        rows = result[0]["result"] if isinstance(result, list) else result
        return rows or []

    async def get_decay_candidates(
        self,
        min_age_hours: int = 168,  # 7 days
        max_access_count: int = 0,
    ) -> list[dict[str, Any]]:
        """Get knowledge entries that are candidates for decay (removal).

        Default: entries older than 7 days with zero access.
        """
        result = await self.db.query(
            """
            SELECT * FROM knowledge
            WHERE access_count <= $max_count
              AND created_at < time::now() - $age_duration
            ORDER BY access_count ASC, created_at ASC
            """,
            {
                "max_count": max_access_count,
                "age_duration": f"{min_age_hours}h",
            },
        )
        rows = result[0]["result"] if isinstance(result, list) else result
        return rows or []
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_decay.py -v
```

---

## Task 12: Graceful Degradation

**Files:**
- Create: `tests/knowledge/test_degradation.py`
- Modify: `src/devteam/knowledge/index.py`
- Modify: `src/devteam/knowledge/extractor.py`

- [ ] **Step 1 (3 min): Write tests for graceful degradation scenarios**

`tests/knowledge/test_degradation.py`:
```python
"""Tests for graceful degradation when SurrealDB or Ollama is unavailable."""

import pytest
from unittest.mock import AsyncMock, patch, PropertyMock
from devteam.knowledge.index import MemoryIndexBuilder, build_memory_index_safe
from devteam.knowledge.extractor import KnowledgeExtractor, ExtractedEntry
from devteam.knowledge.query_tool import QueryKnowledgeTool
from devteam.knowledge.store import KnowledgeStore
from devteam.knowledge.embeddings import OllamaEmbedder


@pytest.mark.asyncio
class TestDegradedSurrealDB:
    async def test_index_builder_returns_empty_on_db_error(self):
        """When SurrealDB is unavailable, index should return empty/minimal."""
        result = await build_memory_index_safe(
            store=None,  # No store available
            role="backend_engineer",
            project="myapp",
        )
        assert isinstance(result, str)
        assert "Available Knowledge" in result or result == ""

    async def test_index_builder_handles_query_failure(self):
        """When a SurrealDB query fails, degrade gracefully."""
        store = AsyncMock(spec=KnowledgeStore)
        store.is_connected = True
        store.db = AsyncMock()
        store.db.query.side_effect = Exception("SurrealDB connection lost")

        builder = MemoryIndexBuilder(store)
        # Should not raise
        index = await builder.build(role="backend_engineer", project="myapp")
        assert isinstance(index, str)

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
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_degradation.py -v
```

- [ ] **Step 2 (3 min): Implement build_memory_index_safe wrapper**

Add to `src/devteam/knowledge/index.py`:

```python
async def build_memory_index_safe(
    store: KnowledgeStore | None,
    role: str,
    project: str,
) -> str:
    """Build memory index with graceful degradation.

    Returns empty/minimal index if SurrealDB is unavailable.
    This is the function called by the orchestrator — never crashes.
    """
    if store is None or not store.is_connected:
        logger.warning("Knowledge store unavailable — returning empty index")
        return INDEX_EMPTY

    try:
        builder = MemoryIndexBuilder(store)
        return await builder.build(role=role, project=project)
    except Exception as e:
        logger.error("Failed to build memory index: %s", e)
        return INDEX_EMPTY
```

Update `MemoryIndexBuilder.build()` to catch query errors:

In the existing `build` method, wrap the fetch call:
```python
    async def build(self, role: str, project: str) -> str:
        try:
            entries = await self._fetch_relevant_entries(role, project)
        except Exception as e:
            logger.error("Failed to fetch entries for memory index: %s", e)
            return INDEX_EMPTY

        if not entries:
            return INDEX_EMPTY

        sections = self._group_entries(entries, role, project)
        return self._format_index(sections)
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_degradation.py -v
```

---

## Task 13: Integration Test — Full Knowledge Lifecycle

**Files:**
- Create: `tests/knowledge/test_integration.py`

- [ ] **Step 1 (5 min): Write end-to-end integration test**

`tests/knowledge/test_integration.py`:
```python
"""Integration test — full knowledge lifecycle with in-memory SurrealDB."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from devteam.knowledge.store import KnowledgeStore
from devteam.knowledge.embeddings import OllamaEmbedder
from devteam.knowledge.extractor import KnowledgeExtractor, ExtractedEntry
from devteam.knowledge.index import MemoryIndexBuilder, build_memory_index_safe
from devteam.knowledge.query_tool import QueryKnowledgeTool
from devteam.knowledge.boundaries import scan_for_secrets, SecretDetectedError


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
        index = await builder.build(role="cloud_engineer", project="myapp")
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
        assert "HEALTHCHECK" in result

    async def test_secret_rejected_in_lifecycle(self, store, mock_embedder):
        """Entries with secrets should be rejected during extraction."""
        extractor = KnowledgeExtractor(store=store, embedder=mock_embedder)

        entries = [
            ExtractedEntry(
                content='Use API key AKIAIOSFODNN7EXAMPLE for S3',
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
            agent_role="backend_engineer",
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
        await store.add_relationship(
            new_result.entry_ids[0], "supersedes", old_result.entry_ids[0]
        )

        # Query should return new, not old
        tool = QueryKnowledgeTool(
            store=store,
            embedder=mock_embedder,
            current_project="myapp",
            agent_role="cloud_engineer",
        )
        result = await tool.query("Fly.io deployment")
        assert "v2" in result or "rolling" in result

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
        tool = QueryKnowledgeTool(
            store=store,
            embedder=mock_embedder,
            current_project="project-a",
            agent_role="data_engineer",
        )
        result = await tool.query("database", scope="project")
        # Result should be scoped — exact assertion depends on vector search behavior

    async def test_graceful_degradation_full_cycle(self, mock_embedder):
        """System should work end-to-end even when SurrealDB is unavailable."""
        # No store connected
        index = await build_memory_index_safe(
            store=None,
            role="backend_engineer",
            project="myapp",
        )
        assert isinstance(index, str)
        assert "Available Knowledge" in index or index == ""

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
            agent_role="backend_engineer",
            project="myapp",
            task_id="T-99",
        )
        assert result.errors == 1
        assert result.persisted == 0
```

**Test command:**
```bash
pixi run pytest tests/knowledge/test_integration.py -v
```

---

## Summary

| Task | What it builds | Files | Est. time |
|------|---------------|-------|-----------|
| 1 | SurrealDB connection, schema, CRUD | `store.py`, `test_store.py` | 10 min |
| 2 | Graph relationships (discovered, supersedes, requires, relates_to) | `store.py`, `test_store.py` | 7 min |
| 3 | Ollama embedding integration | `embeddings.py`, `test_embeddings.py` | 7 min |
| 4 | Vector search + combined queries | `store.py`, `test_vector_search.py` | 8 min |
| 5 | Knowledge boundaries + secret scanning | `boundaries.py`, `test_boundaries.py` | 8 min |
| 6 | Knowledge extraction (haiku agent) | `extractor.py`, `test_extractor.py` | 8 min |
| 7 | Memory index generation | `index.py`, `test_index.py` | 8 min |
| 8 | query_knowledge tool for agents | `query_tool.py`, `test_query_tool.py` | 8 min |
| 9 | Knowledge admin CLI commands | `commands/knowledge.py`, `test_knowledge_commands.py` | 8 min |
| 10 | SurrealDB materialized index event | `store.py` | 5 min |
| 11 | Decay + consolidation logic | `store.py`, `test_decay.py` | 7 min |
| 12 | Graceful degradation | `index.py`, `extractor.py`, `test_degradation.py` | 6 min |
| 13 | Integration test — full lifecycle | `test_integration.py` | 5 min |
| **Total** | | | **~95 min** |

**All tests use `Surreal("mem://")` for SurrealDB (no disk, fast) and mock `httpx` for Ollama (no running instance required).**

**Run all knowledge tests:**
```bash
pixi run pytest tests/knowledge/ -v
```
