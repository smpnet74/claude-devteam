"""Memory index generation -- compact topic summary injected into agent context."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from devteam.knowledge.store import KnowledgeStore

logger = logging.getLogger(__name__)

INDEX_HEADER = (
    "## Available Knowledge\nYou can query the knowledge base for details on any of these topics.\n"
)
INDEX_EMPTY = (
    "## Available Knowledge\n"
    "No knowledge entries yet. The knowledge base will grow as the team works.\n"
)


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

    async def _fetch_relevant_entries(self, role: str, project: str) -> list[dict[str, Any]]:
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
        # SurrealDB mem:// returns a list of rows directly
        if isinstance(result, list) and result and isinstance(result[0], dict):
            # Check if it looks like a wrapped response or direct rows
            if "result" in result[0] and len(result) == 1:
                rows = result[0]["result"]
            else:
                rows = result
        else:
            rows = result
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

            # Group by topic (using summary as topic key)
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
