"""SurrealDB knowledge store -- connection, schema, CRUD, graph, and vector search."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from surrealdb import AsyncSurreal, RecordID

from devteam.knowledge.embeddings import EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)

# Valid graph relation types between knowledge entries.
VALID_RELATIONS = frozenset({"discovered", "supersedes", "requires", "relates_to"})

# Schema definition for the knowledge table.
# Executed idempotently on every connect.
SCHEMA_STATEMENTS = [
    "DEFINE TABLE IF NOT EXISTS knowledge SCHEMAFULL",
    "DEFINE FIELD IF NOT EXISTS content ON knowledge TYPE string",
    "DEFINE FIELD IF NOT EXISTS summary ON knowledge TYPE string",
    "DEFINE FIELD IF NOT EXISTS source ON knowledge TYPE option<object>",
    "DEFINE FIELD IF NOT EXISTS tags ON knowledge TYPE array<string>",
    "DEFINE FIELD IF NOT EXISTS sharing ON knowledge TYPE string",
    "DEFINE FIELD IF NOT EXISTS project ON knowledge TYPE option<string>",
    "DEFINE FIELD IF NOT EXISTS embedding ON knowledge TYPE array<float>",
    "DEFINE FIELD IF NOT EXISTS created_at ON knowledge TYPE datetime",
    "DEFINE FIELD IF NOT EXISTS verified ON knowledge TYPE bool DEFAULT false",
    "DEFINE FIELD IF NOT EXISTS access_count ON knowledge TYPE int DEFAULT 0",
    f"DEFINE INDEX IF NOT EXISTS knowledge_vec ON knowledge FIELDS embedding HNSW DIMENSION {EMBEDDING_DIMENSIONS} DIST COSINE",
    # Materialized index table for fast stats lookups
    "DEFINE TABLE IF NOT EXISTS knowledge_index SCHEMAFULL",
    "DEFINE FIELD IF NOT EXISTS sections ON knowledge_index TYPE option<array>",
    "DEFINE FIELD IF NOT EXISTS entry_count ON knowledge_index TYPE int DEFAULT 0",
    "DEFINE FIELD IF NOT EXISTS rebuilt_at ON knowledge_index TYPE option<datetime>",
    # Event: refresh the materialized index on every knowledge write
    """DEFINE EVENT IF NOT EXISTS refresh_index ON knowledge
        WHEN $event IN ["CREATE", "UPDATE", "DELETE"]
        THEN {
            LET $stats = (SELECT tags, count() AS cnt, math::max(created_at) AS last_updated FROM knowledge GROUP BY tags);
            LET $total = (SELECT count() AS cnt FROM knowledge GROUP ALL);
            UPSERT knowledge_index:current SET
                sections = $stats,
                entry_count = $total[0].cnt OR 0,
                rebuilt_at = time::now();
        }""",
]


_UPDATABLE_FIELDS = frozenset(
    {
        "content",
        "summary",
        "tags",
        "sharing",
        "project",
        "embedding",
        "verified",
        "source",
        "access_count",
    }
)


class KnowledgeStore:
    """Manages SurrealDB connection and knowledge CRUD operations.

    Args:
        url: SurrealDB connection URL. Use ``"mem://"`` for in-memory (testing)
             or ``"ws://localhost:8000"`` for the Docker-hosted server.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.db: AsyncSurreal = AsyncSurreal(url)  # type: ignore[assignment]
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(
        self,
        namespace: str = "devteam",
        database: str = "knowledge",
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        """Connect to SurrealDB and initialize schema.

        Args:
            namespace: SurrealDB namespace to use.
            database: SurrealDB database to use.
            username: Optional username for authentication (required for ws:// connections).
            password: Optional password for authentication (required for ws:// connections).
        """
        if self._connected:
            return
        try:
            await self.db.connect()

            # Authenticate if credentials provided (required for ws:// connections)
            if username and password:
                await self.db.signin({"username": username, "password": password})

            await self.db.use(namespace, database)
            await self._init_schema()
            self._connected = True
            logger.info("Knowledge store connected: %s", self.url)
        except Exception as e:
            self._connected = False
            raise ConnectionError(f"Failed to connect to SurrealDB at {self.url}: {e}") from e

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

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

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
        """Create a knowledge entry. Returns the record ID as a string."""
        if not content:
            raise ValueError("content must not be empty")
        if sharing not in ("shared", "project"):
            raise ValueError(f"sharing must be 'shared' or 'project', got: {sharing!r}")
        if sharing == "project" and not project:
            raise ValueError("project must be set when sharing='project'")
        if embedding is not None and len(embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Embedding must be {EMBEDDING_DIMENSIONS} dimensions, got {len(embedding)}"
            )

        record = await self.db.create(
            "knowledge",
            {
                "content": content,
                "summary": summary,
                "source": source,
                "tags": tags,
                "sharing": sharing,
                "project": project,
                "embedding": embedding,
                "created_at": datetime.now(timezone.utc),
                "verified": False,
                "access_count": 0,
            },
        )
        # create() returns a dict on success; a string on error.
        if isinstance(record, str):
            raise RuntimeError(f"SurrealDB create failed: {record}")
        record_id = record["id"] if isinstance(record, dict) else record[0]["id"]
        return str(record_id)

    async def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        """Get a single knowledge entry by ID. Returns None if not found."""
        rid = self._parse_record_id(entry_id)
        rows = await self.db.query("SELECT * FROM $id", {"id": rid})
        if not rows:
            return None
        row = rows[0] if isinstance(rows, list) else rows
        # Convert RecordID to string for the caller
        return self._normalize_row(row)

    async def update_entry(self, entry_id: str, **fields: Any) -> None:
        """Update specific fields on a knowledge entry.

        Validates the same invariants as create_entry for the fields being
        updated (sharing value, project requirement, embedding dimensions).
        """
        if not fields:
            return
        invalid = set(fields) - _UPDATABLE_FIELDS
        if invalid:
            raise ValueError(f"Cannot update fields: {invalid}")

        # Validate sharing value
        if "sharing" in fields:
            if fields["sharing"] not in ("shared", "project"):
                raise ValueError(
                    f"sharing must be 'shared' or 'project', got: {fields['sharing']!r}"
                )

        # Validate project is set when sharing='project'
        if "sharing" in fields and fields["sharing"] == "project":
            # project must be provided in this update, or already exist on the entry
            if "project" not in fields or not fields["project"]:
                existing = await self.get_entry(entry_id)
                if not existing or not existing.get("project"):
                    raise ValueError("project must be set when sharing='project'")

        # Validate embedding dimensions
        if "embedding" in fields:
            emb = fields["embedding"]
            if emb is not None and len(emb) == 0:
                raise ValueError(f"Embedding must be {EMBEDDING_DIMENSIONS} dimensions, got 0")
            if emb is not None and len(emb) != EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"Embedding must be {EMBEDDING_DIMENSIONS} dimensions, got {len(emb)}"
                )

        rid = self._parse_record_id(entry_id)
        set_clauses = ", ".join(f"{k} = ${k}" for k in fields)
        await self.db.query(
            f"UPDATE $id SET {set_clauses}",
            {"id": rid, **fields},
        )

    async def increment_access_count(self, entry_id: str) -> None:
        """Increment the access_count for a knowledge entry."""
        rid = self._parse_record_id(entry_id)
        await self.db.query("UPDATE $id SET access_count += 1", {"id": rid})

    async def delete_entry(self, entry_id: str) -> None:
        """Delete a knowledge entry by ID."""
        rid = self._parse_record_id(entry_id)
        await self.db.query("DELETE $id", {"id": rid})

    async def delete_by_project(self, project: str) -> int:
        """Delete all entries scoped to a project. Returns count deleted."""
        rows = await self.db.query(
            "SELECT count() AS total FROM knowledge WHERE project = $project GROUP ALL",
            {"project": project},
        )
        count = rows[0]["total"] if rows else 0
        await self.db.query(
            "DELETE FROM knowledge WHERE project = $project",
            {"project": project},
        )
        return count

    async def list_entries(
        self,
        sharing: str | None = None,
        project: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List knowledge entries with optional filters.

        Used by the memory index builder to fetch entries without reaching
        through to the raw database connection.

        Args:
            sharing: Filter by sharing scope (``"shared"`` or ``"project"``).
            project: Include shared entries and project-scoped entries for this project.
            limit: Maximum number of entries to return.

        Returns:
            List of entry dicts, ordered by created_at descending.
        """
        filters: list[str] = []
        params: dict[str, Any] = {}

        if sharing:
            filters.append("sharing = $sharing")
            params["sharing"] = sharing
        elif project:
            filters.append('(sharing = "shared" OR project = $project)')
            params["project"] = project

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        query = f"""
            SELECT summary, tags, sharing, project, verified, created_at
            FROM knowledge
            {where_clause}
            ORDER BY created_at DESC
            LIMIT {limit}
        """
        result = await self.db.query(query, params)
        # SurrealDB mem:// returns a list of rows directly
        if isinstance(result, list) and result and isinstance(result[0], dict):
            if "result" in result[0] and len(result) == 1:
                rows = result[0]["result"]
            else:
                rows = result
        else:
            rows = result
        return rows or []

    async def get_stats(self) -> dict[str, Any]:
        """Get knowledge base statistics (total count)."""
        rows = await self.db.query("SELECT count() AS total FROM knowledge GROUP ALL")
        total = rows[0]["total"] if rows else 0
        return {"total": total}

    async def get_stats_detailed(self) -> dict[str, Any]:
        """Get detailed knowledge base statistics."""
        # Total count
        total_rows = await self.db.query("SELECT count() AS total FROM knowledge GROUP ALL")
        total = total_rows[0]["total"] if total_rows else 0

        # Count by sharing scope
        sharing_rows = await self.db.query(
            "SELECT sharing, count() AS cnt FROM knowledge GROUP BY sharing"
        )
        by_sharing: dict[str, int] = {}
        for row in sharing_rows or []:
            by_sharing[row["sharing"]] = row["cnt"]

        # Count by project (non-null only)
        project_rows = await self.db.query(
            "SELECT project, count() AS cnt FROM knowledge WHERE project IS NOT NONE GROUP BY project"
        )
        by_project: dict[str, int] = {}
        for row in project_rows or []:
            if row.get("project"):
                by_project[row["project"]] = row["cnt"]

        # Verified count
        verified_rows = await self.db.query(
            "SELECT count() AS cnt FROM knowledge WHERE verified = true GROUP ALL"
        )
        verified = verified_rows[0]["cnt"] if verified_rows else 0

        return {
            "total": total,
            "verified": verified,
            "by_sharing": by_sharing,
            "by_project": by_project,
        }

    # ------------------------------------------------------------------
    # Materialized index
    # ------------------------------------------------------------------

    async def get_materialized_index(self) -> dict[str, Any] | None:
        """Get the pre-computed materialized index record.

        The materialized index is updated automatically by a SurrealDB event
        whenever a knowledge entry is created, updated, or deleted.
        """
        result = await self.db.query("SELECT * FROM knowledge_index:current")
        if not result:
            return None
        row = result[0] if isinstance(result, list) else result
        if isinstance(row, dict) and "result" in row:
            rows = row["result"]
            return rows[0] if rows else None
        return row if isinstance(row, dict) and row.get("entry_count") is not None else None

    # ------------------------------------------------------------------
    # Decay & consolidation queries
    # ------------------------------------------------------------------

    async def get_entries_by_access_count(
        self,
        min_count: int | None = None,
        max_count: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get entries filtered by access count range."""
        conditions: list[str] = []
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
        rows = self._extract_rows(result)
        return [self._normalize_row(r) for r in rows]

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
              AND verified = false
            ORDER BY access_count ASC, created_at ASC
            """,
            {
                "max_count": max_access_count,
                "age_duration": f"{min_age_hours}h",
            },
        )
        rows = self._extract_rows(result)
        return [self._normalize_row(r) for r in rows]

    async def get_superseded_ids(self) -> list[str]:
        """Return IDs of all knowledge entries that have been superseded."""
        rows = await self.db.query("SELECT ->supersedes.out AS superseded FROM knowledge")
        superseded: set[str] = set()
        for row in rows or []:
            for item in row.get("superseded") or []:
                superseded.add(str(item))
        return list(superseded)

    # ------------------------------------------------------------------
    # Graph relationship operations
    # ------------------------------------------------------------------

    async def add_relationship(self, from_id: str, relation: str, to_id: str) -> None:
        """Create a graph edge between two records.

        Valid relations: discovered, supersedes, requires, relates_to.
        """
        if relation not in VALID_RELATIONS:
            raise ValueError(
                f"Invalid relation {relation!r}. Must be one of: {sorted(VALID_RELATIONS)}"
            )
        from_rid = self._parse_record_id(from_id)
        to_rid = self._parse_record_id(to_id)
        # SurrealQL RELATE uses -> syntax with the relation name as table
        await self.db.query(
            f"RELATE $from->{relation}->$to",
            {"from": from_rid, "to": to_rid},
        )

    async def get_relationships(
        self,
        entry_id: str,
        direction: str = "out",
        relation: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get graph relationships for an entry.

        Args:
            entry_id: The record ID (e.g. ``"knowledge:abc"``).
            direction: ``"in"`` for incoming edges, ``"out"`` for outgoing edges.
            relation: Optional relation type filter.

        Returns:
            List of dicts with ``"id"`` of the related record.
        """
        rid = self._parse_record_id(entry_id)
        if direction not in ("in", "out"):
            raise ValueError(f"direction must be 'in' or 'out', got {direction!r}")
        if relation and relation not in VALID_RELATIONS:
            raise ValueError(f"Unknown relation type: {relation!r}")
        key = "items"

        if relation:
            if direction == "out":
                query = f"SELECT ->{relation}.out AS {key} FROM $id"
            else:
                query = f"SELECT <-{relation}.in AS {key} FROM $id"
        else:
            # Query all known relation types and merge results
            if direction == "out":
                selects = ", ".join(f"->{r}.out AS {r}_out" for r in VALID_RELATIONS)
            else:
                selects = ", ".join(f"<-{r}.in AS {r}_in" for r in VALID_RELATIONS)
            query = f"SELECT {selects} FROM $id"
            rows = await self.db.query(query, {"id": rid})
            if not rows:
                return []
            row = rows[0] if isinstance(rows, list) else rows
            combined: list[dict[str, Any]] = []
            for col_key, values in row.items():
                if col_key == "id" or not isinstance(values, list):
                    continue
                for item in values:
                    combined.append({"id": str(item)})
            return combined

        rows = await self.db.query(query, {"id": rid})
        if not rows:
            return []

        row = rows[0] if isinstance(rows, list) else rows
        items = row.get(key, [])
        return [{"id": str(item)} for item in items] if items else []

    # ------------------------------------------------------------------
    # Vector search
    # ------------------------------------------------------------------

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

        Uses SurrealDB's native ``<|K,EF|>`` KNN operator for HNSW index
        utilization instead of a full table scan with ORDER BY.

        Args:
            embedding: Query embedding vector (768 dimensions).
            limit: Maximum number of results.
            sharing: Filter by sharing scope (``"shared"`` or ``"project"``).
            project: Include shared + project-scoped for this project.
            tags: Filter to entries containing any of these tags.
            exclude_superseded: Exclude entries that have been superseded.

        Returns:
            List of matching entries sorted by relevance (descending).
        """
        if len(embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Search vector must be {EMBEDDING_DIMENSIONS} dimensions, got {len(embedding)}"
            )

        filters: list[str] = []
        params: dict[str, Any] = {"vec": embedding}

        if sharing:
            filters.append("sharing = $sharing")
            params["sharing"] = sharing
        elif project:
            filters.append('(sharing = "shared" OR project = $project)')
            params["project"] = project

        if tags:
            tag_parts = []
            for i, tag in enumerate(tags):
                param_name = f"tag_{i}"
                tag_parts.append(f"tags CONTAINS ${param_name}")
                params[param_name] = tag
            filters.append(f"({' OR '.join(tag_parts)})")

        # Exclude superseded entries in the WHERE clause rather than
        # post-filtering, so we always get the requested number of results
        # (when available).
        # NOTE: An inline subquery (SELECT VALUE out FROM supersedes) inside
        # a WHERE clause breaks the KNN operator in SurrealDB, so we
        # pre-fetch superseded IDs and pass them as a parameter instead.
        knn_k = limit
        if exclude_superseded:
            sup_ids = await self.db.query("SELECT VALUE out FROM supersedes")
            params["sup"] = sup_ids if sup_ids else []
            filters.append("id NOT IN $sup")
            # Cap over-fetch to avoid cost scaling with total history.
            # Ideally we'd scope superseded IDs to the query's neighbourhood,
            # but that requires a two-pass search.  This bounds the worst case
            # while still giving good results when the superseded set is small.
            MAX_SUPERSEDE_OVERFETCH = 50
            overfetch = min(len(params["sup"]), MAX_SUPERSEDE_OVERFETCH)
            knn_k = limit + overfetch

        # KNN operator: <|K,EF|> where K = number of neighbours, EF = search
        # depth (higher EF = more accurate but slower).  The HNSW index defined
        # on the embedding field is utilised automatically.
        ef = max(knn_k * 8, 40)  # reasonable default for search depth
        filters.append(f"embedding <|{knn_k},{ef}|> $vec")

        where_clause = " AND ".join(filters)
        where_sql = f"WHERE {where_clause}"

        query = f"""
            SELECT *,
                vector::similarity::cosine(embedding, $vec) AS relevance
            FROM knowledge
            {where_sql}
            ORDER BY relevance DESC
            LIMIT {limit}
        """

        rows = await self.db.query(query, params)
        if not rows:
            return []

        return [self._normalize_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_record_id(entry_id: str) -> RecordID | str:
        """Convert a string ID like ``"knowledge:abc"`` to a RecordID.

        If the string doesn't contain a colon, return it as-is (e.g.
        ``"agent:cloud_engineer"`` passes through for RELATE queries).
        """
        if isinstance(entry_id, RecordID):
            return entry_id
        if ":" in entry_id:
            table, record = entry_id.split(":", 1)
            return RecordID(table, record)
        return entry_id

    @staticmethod
    def _extract_rows(result: Any) -> list[dict[str, Any]]:
        """Extract a list of row dicts from a SurrealDB query response.

        SurrealDB mem:// returns rows directly as ``list[dict]``.
        Some versions wrap them in ``[{"result": [...]}]``.
        """
        if not result:
            return []
        # Unwrap [{"result": [...], "status": "OK"}] envelope
        if (
            isinstance(result, list)
            and len(result) == 1
            and isinstance(result[0], dict)
            and "result" in result[0]
        ):
            inner = result[0]["result"]
            return inner if isinstance(inner, list) else []
        # Already a plain list of row dicts
        if isinstance(result, list) and result and isinstance(result[0], dict):
            return result
        return []

    @staticmethod
    def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        """Convert RecordID fields to strings and fill optional fields."""
        result = dict(row)
        if "id" in result and isinstance(result["id"], RecordID):
            result["id"] = str(result["id"])
        # Ensure optional fields are present (SurrealDB omits None option fields)
        result.setdefault("project", None)
        result.setdefault("source", None)
        return result
