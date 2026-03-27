"""Knowledge extraction -- haiku agent extracts learnings from agent output."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel

from devteam.knowledge.boundaries import (
    SharingScope,
    SecretDetectedError,
    determine_sharing_scope,
    scan_for_secrets,
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
                    "Knowledge entry rejected (secret detected): %s -- %s",
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
                    # Non-fatal -- entry is still persisted
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
