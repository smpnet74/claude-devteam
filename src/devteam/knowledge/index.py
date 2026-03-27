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


def _sanitize_summary(text: str) -> str:
    """Strip characters that could break index formatting or inject instructions."""
    # Remove newlines (prevents breaking out of bullet list)
    text = text.replace("\n", " ").replace("\r", "")
    # Remove markdown heading markers
    text = text.lstrip("#").strip()
    return text


class MemoryIndexBuilder:
    """Builds a compact memory index from SurrealDB for agent context injection.

    The index shows topics and entry counts, not full content.
    Agents use the query_knowledge tool to retrieve details.
    """

    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    async def build(self, project: str) -> str:
        """Build a memory index for the given project.

        Role-scoped indexing is deferred; will be added when role-based
        filtering is implemented in the store layer.

        Args:
            project: Current project name.

        Returns:
            Formatted markdown string suitable for agent context injection.
            Stays compact (~30-50 lines) regardless of knowledge base size.
        """
        try:
            entries = await self._fetch_relevant_entries(project)
        except Exception as e:
            logger.error("Failed to fetch entries for memory index: %s", e)
            return INDEX_EMPTY

        if not entries:
            return INDEX_EMPTY

        sections = self._group_entries(entries, project)
        return self._format_index(sections)

    async def _fetch_relevant_entries(self, project: str) -> list[dict[str, Any]]:
        """Fetch entries visible to this project."""
        return await self.store.list_entries(project=project, limit=200)

    def _group_entries(
        self,
        entries: list[dict[str, Any]],
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
                summary = _sanitize_summary(entry.get("summary", "Unknown"))
                topic_counts[summary] += 1
                if entry.get("verified"):
                    topic_verified[summary] = True

            max_topics = 10
            topic_items = list(topic_counts.items())
            for topic, count in topic_items[:max_topics]:
                suffix_parts = []
                if count > 1:
                    suffix_parts.append(f"{count} entries")
                else:
                    suffix_parts.append("1 entry")
                if topic_verified.get(topic):
                    suffix_parts.append("verified")
                suffix = ", ".join(suffix_parts)
                lines.append(f"- {topic} ({suffix})")

            overflow = len(topic_items) - max_topics
            if overflow > 0:
                lines.append(f"- ... and {overflow} more topics")

            lines.append("")  # blank line between sections

        return "\n".join(lines)


async def build_memory_index_safe(
    store: KnowledgeStore | None,
    project: str,
) -> str:
    """Build memory index with graceful degradation.

    Returns empty/minimal index if SurrealDB is unavailable.
    This is the function called by the orchestrator -- never crashes.
    """
    if store is None or not store.is_connected:
        logger.warning("Knowledge store unavailable -- returning empty index")
        return INDEX_EMPTY

    try:
        builder = MemoryIndexBuilder(store)
        return await builder.build(project=project)
    except Exception as e:
        logger.error("Failed to build memory index: %s", e)
        return INDEX_EMPTY
