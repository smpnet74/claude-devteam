"""query_knowledge tool -- on-demand knowledge search exposed to agents."""

from __future__ import annotations

import logging
from typing import Any

from devteam.knowledge.boundaries import apply_scope_filter
from devteam.knowledge.embeddings import OllamaEmbedder
from devteam.knowledge.store import KnowledgeStore

logger = logging.getLogger(__name__)

RELEVANCE_THRESHOLD = 0.3  # minimum cosine similarity to include

# Map agent roles to their relevant domain tags for my_role scope filtering.
_ROLE_DOMAIN_TAGS: dict[str, list[str]] = {
    "backend_engineer": ["backend", "api", "database"],
    "frontend_engineer": ["frontend", "ui", "css"],
    "devops_engineer": ["devops", "ci", "deployment"],
    "data_engineer": ["data", "pipeline", "etl"],
    "infra_engineer": ["infra", "infrastructure", "networking"],
    "tooling_engineer": ["tooling", "build", "automation"],
    "cloud_engineer": ["cloud", "aws", "gcp", "azure"],
    "chief_architect": ["architecture", "design", "system"],
    "em_team_a": ["process", "team", "management"],
    "em_team_b": ["process", "team", "management"],
    "planner_researcher_a": ["research", "planning", "analysis"],
    "planner_researcher_b": ["research", "planning", "analysis"],
    "tech_writer": ["documentation", "docs", "writing"],
    "qa_engineer": ["testing", "quality", "validation"],
    "security_engineer": ["security", "auth", "encryption"],
}


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
                "Knowledge query unavailable -- embedding service is down. "
                "Proceed without knowledge base consultation."
            )

        # Build scope filters via centralized boundary logic
        scope_filter = apply_scope_filter(
            scope=scope, project=self.current_project, role=self.agent_role
        )
        sharing = scope_filter.get("sharing")
        project = scope_filter.get("project")
        tags = None

        if scope == "my_role":
            # Use domain tags for the role rather than the role name itself
            tags = _ROLE_DOMAIN_TAGS.get(self.agent_role, [self.agent_role])

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
        relevant = [r for r in results if r.get("relevance", 0) >= RELEVANCE_THRESHOLD]

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
            lines.append(
                f"**Scope:** {sharing} | **Status:** {status} | **Relevance:** {relevance:.2f}"
            )
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
