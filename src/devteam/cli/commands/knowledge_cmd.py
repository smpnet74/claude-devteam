"""devteam knowledge — admin commands for the knowledge base.

Commands:
    search   — Semantic search of the knowledge base
    stats    — Show knowledge base statistics
    verify   — Mark an entry as verified
    redact   — Redact content from an entry (preserves metadata)
    purge    — Delete entries entirely (by ID or by project)
    export   — Export knowledge base to JSON
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import typer

logger = logging.getLogger(__name__)

knowledge_app = typer.Typer(
    name="knowledge",
    help="Knowledge base administration commands.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Module-level store singleton (lazy-connected via async helper)
# ---------------------------------------------------------------------------

_store: Any = None  # KnowledgeStore | None
_embedder: Any = None  # OllamaEmbedder | None


async def _ensure_connected() -> tuple[Any, Any]:
    """Get the KnowledgeStore and embedder, connecting on first call."""
    from devteam.config.settings import load_global_config
    from devteam.knowledge.embeddings import create_embedder_from_config
    from devteam.knowledge.store import KnowledgeStore

    global _store, _embedder
    if _store is None:
        config_path = Path.home() / ".devteam" / "config.toml"
        config = load_global_config(config_path)
        _store = KnowledgeStore(config.knowledge.surrealdb_url)
        await _store.connect(
            username=config.knowledge.surrealdb_username,
            password=config.knowledge.surrealdb_password,
        )
        _embedder = create_embedder_from_config(config.knowledge)
    return _store, _embedder


# ---------------------------------------------------------------------------
# Async implementations — one per command, single event-loop lifetime
# ---------------------------------------------------------------------------


async def _search_impl(
    query: str, scope: str, project: str | None, limit: int
) -> str:
    from devteam.knowledge.query_tool import QueryKnowledgeTool

    store, embedder = await _ensure_connected()
    tool = QueryKnowledgeTool(
        store=store,
        embedder=embedder,
        current_project=project or "",
        agent_role="admin",
    )
    return await tool.query(query, scope=scope, limit=limit)


async def _stats_impl() -> dict[str, Any]:
    store, _emb = await _ensure_connected()
    return await store.get_stats_detailed()


async def _verify_impl(entry_id: str) -> None:
    store, _emb = await _ensure_connected()
    await store.update_entry(entry_id, verified=True)


async def _redact_impl(entry_id: str) -> dict[str, Any] | None:
    from devteam.knowledge.embeddings import EMBEDDING_DIMENSIONS

    store, _emb = await _ensure_connected()
    entry = await store.get_entry(entry_id)
    if not entry:
        return None
    await store.update_entry(
        entry_id,
        content="[REDACTED]",
        embedding=[0.0] * EMBEDDING_DIMENSIONS,
    )
    return entry


async def _purge_impl(
    entry_id: str | None, project: str | None, older_than: int | None
) -> str:
    store, _emb = await _ensure_connected()

    if older_than is not None:
        candidates = await store.get_decay_candidates(
            min_age_hours=older_than * 24,
            max_access_count=0,
        )
        if not candidates:
            return "No entries match purge criteria."
        for c in candidates:
            await store.delete_entry(str(c["id"]))
        return f"Purged {len(candidates)} stale entries older than {older_than} days."
    elif project:
        count = await store.delete_by_project(project)
        return f"Purged {count} entries for project '{project}'."
    elif entry_id:
        await store.delete_entry(entry_id)
        return f"Purged entry {entry_id}."
    else:
        return ""


async def _export_impl(project: str | None) -> list[dict[str, Any]]:
    store, _emb = await _ensure_connected()
    return await store.list_all_entries(project=project)


# ---------------------------------------------------------------------------
# Commands — each calls asyncio.run() exactly once
# ---------------------------------------------------------------------------


@knowledge_app.command()
def search(
    query: str = typer.Argument(..., help="Semantic search query"),
    scope: str = typer.Option("all", help="Scope: shared, project, all"),
    project: str | None = typer.Option(None, help="Project name filter"),
    limit: int = typer.Option(5, help="Max results"),
) -> None:
    """Semantic search of the knowledge base."""
    try:
        result = asyncio.run(_search_impl(query, scope, project, limit))
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(result)


@knowledge_app.command()
def stats() -> None:
    """Show knowledge base statistics."""
    try:
        s = asyncio.run(_stats_impl())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo("Knowledge Base Statistics")
    typer.echo("=" * 40)
    typer.echo(f"Total entries:    {s['total']}")
    typer.echo(f"Verified:         {s['verified']}")
    typer.echo()
    typer.echo("By sharing scope:")
    for scope_name, count in s.get("by_sharing", {}).items():
        typer.echo(f"  {scope_name}: {count}")
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
    try:
        asyncio.run(_verify_impl(entry_id))
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Marked {entry_id} as verified.")


@knowledge_app.command()
def redact(
    entry_id: str = typer.Argument(..., help="Entry ID to redact"),
) -> None:
    """Remove sensitive content from an entry, preserving the learning."""
    try:
        entry = asyncio.run(_redact_impl(entry_id))
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if entry is None:
        typer.echo(f"Entry {entry_id} not found.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Redacted content from {entry_id}. Summary preserved: {entry.get('summary', '')}")


@knowledge_app.command()
def purge(
    entry_id: str | None = typer.Argument(None, help="Entry ID to delete"),
    project: str | None = typer.Option(None, help="Delete all entries for a project"),
    older_than: int | None = typer.Option(
        None, "--older-than", help="Purge unverified entries older than N days"
    ),
) -> None:
    """Delete knowledge entries entirely."""
    if not any([entry_id, project, older_than is not None]):
        typer.echo("Provide either an entry ID, --project, or --older-than.", err=True)
        raise typer.Exit(1)

    try:
        msg = asyncio.run(_purge_impl(entry_id, project, older_than))
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(msg)


@knowledge_app.command(name="export")
def export_knowledge(
    project: str | None = typer.Option(None, help="Export only this project's knowledge"),
    output: str | None = typer.Option(None, "-o", help="Output file path"),
) -> None:
    """Export knowledge base to JSON."""
    try:
        entries = asyncio.run(_export_impl(project))
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    # Remove embeddings from export (large, not human-readable)
    for entry in entries:
        entry.pop("embedding", None)
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
        output_path = Path(output)
        try:
            output_path.write_text(json_str)
        except OSError as e:
            typer.echo(f"Error writing to {output_path}: {e}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"Exported {len(entries)} entries to {output}")
    else:
        typer.echo(json_str)
