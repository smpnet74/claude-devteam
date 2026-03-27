"""SurrealDB knowledge store -- connection, schema, CRUD, graph, and vector search."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from surrealdb import AsyncSurreal, RecordID

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
    "DEFINE INDEX IF NOT EXISTS knowledge_vec ON knowledge FIELDS embedding HNSW DIMENSION 768 DIST COSINE",
]


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
        """Update specific fields on a knowledge entry."""
        if not fields:
            return
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

    async def get_superseded_ids(self) -> list[str]:
        """Return IDs of all knowledge entries that have been superseded."""
        rows = await self.db.query("SELECT ->supersedes.out AS superseded FROM knowledge")
        superseded: set[str] = set()
        for row in rows or []:
            for item in row.get("superseded") or []:
                superseded.add(str(item))
        return list(superseded)

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
        filters: list[str] = []
        params: dict[str, Any] = {"vec": embedding, "limit": limit}

        if sharing:
            filters.append("sharing = $sharing")
            params["sharing"] = sharing
        elif project:
            filters.append('(sharing = "shared" OR project = $project)')
            params["project"] = project

        if tags:
            tag_conditions = " OR ".join(f"tags CONTAINS '{tag}'" for tag in tags)
            filters.append(f"({tag_conditions})")

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

        rows = await self.db.query(query, params)
        if not rows:
            return []

        # Post-process: exclude superseded entries
        if exclude_superseded:
            superseded_ids = await self.get_superseded_ids()
            if superseded_ids:
                rows = [r for r in rows if str(r.get("id", "")) not in superseded_ids]

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
    def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        """Convert RecordID fields to strings and fill optional fields."""
        result = dict(row)
        if "id" in result and isinstance(result["id"], RecordID):
            result["id"] = str(result["id"])
        # Ensure optional fields are present (SurrealDB omits None option fields)
        result.setdefault("project", None)
        result.setdefault("source", None)
        return result
