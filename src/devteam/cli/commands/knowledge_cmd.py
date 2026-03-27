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
from typing import Any

import typer

logger = logging.getLogger(__name__)

knowledge_app = typer.Typer(
    name="knowledge",
    help="Knowledge base administration commands.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Module-level store singleton (lazy-connected, like job_cmd)
# ---------------------------------------------------------------------------

_store: Any = None  # KnowledgeStore | None


def get_store() -> Any:
    """Get the KnowledgeStore instance. Overridden in tests."""
    from devteam.knowledge.store import KnowledgeStore

    global _store
    if _store is None:
        _store = KnowledgeStore("ws://localhost:8000")
        _run(_store.connect())
    return _store


def _run(coro: Any) -> Any:
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


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@knowledge_app.command()
def search(
    query: str = typer.Argument(..., help="Semantic search query"),
    scope: str = typer.Option("all", help="Scope: shared, project, all"),
    project: str | None = typer.Option(None, help="Project name filter"),
    limit: int = typer.Option(5, help="Max results"),
) -> None:
    """Semantic search of the knowledge base."""
    from devteam.knowledge.embeddings import OllamaEmbedder
    from devteam.knowledge.query_tool import QueryKnowledgeTool

    try:
        store = get_store()
    except Exception as e:
        typer.echo(f"Knowledge store unavailable: {e}", err=True)
        raise typer.Exit(1)

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
    try:
        store = get_store()
    except Exception as e:
        typer.echo(f"Knowledge store unavailable: {e}", err=True)
        raise typer.Exit(1)

    s = _run(store.get_stats_detailed())

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
        store = get_store()
    except Exception as e:
        typer.echo(f"Knowledge store unavailable: {e}", err=True)
        raise typer.Exit(1)

    _run(store.update_entry(entry_id, verified=True))
    typer.echo(f"Marked {entry_id} as verified.")


@knowledge_app.command()
def redact(
    entry_id: str = typer.Argument(..., help="Entry ID to redact"),
) -> None:
    """Remove sensitive content from an entry, preserving the learning."""
    try:
        store = get_store()
    except Exception as e:
        typer.echo(f"Knowledge store unavailable: {e}", err=True)
        raise typer.Exit(1)

    entry = _run(store.get_entry(entry_id))
    if not entry:
        typer.echo(f"Entry {entry_id} not found.", err=True)
        raise typer.Exit(1)

    from devteam.knowledge.embeddings import EMBEDDING_DIMENSIONS

    _run(
        store.update_entry(
            entry_id,
            content="[REDACTED]",
            embedding=[0.0] * EMBEDDING_DIMENSIONS,
        )
    )
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
    try:
        store = get_store()
    except Exception as e:
        typer.echo(f"Knowledge store unavailable: {e}", err=True)
        raise typer.Exit(1)

    if older_than is not None:
        candidates = _run(
            store.get_decay_candidates(
                min_age_hours=older_than * 24,
                max_access_count=0,
            )
        )
        if not candidates:
            typer.echo("No entries match purge criteria.")
            return
        for c in candidates:
            _run(store.delete_entry(str(c["id"])))
        typer.echo(f"Purged {len(candidates)} stale entries older than {older_than} days.")
    elif project:
        count = _run(store.delete_by_project(project))
        typer.echo(f"Purged {count} entries for project '{project}'.")
    elif entry_id:
        _run(store.delete_entry(entry_id))
        typer.echo(f"Purged entry {entry_id}.")
    else:
        typer.echo("Provide either an entry ID, --project, or --older-than.", err=True)
        raise typer.Exit(1)


@knowledge_app.command(name="export")
def export_knowledge(
    project: str | None = typer.Option(None, help="Export only this project's knowledge"),
    output: str | None = typer.Option(None, "-o", help="Output file path"),
) -> None:
    """Export knowledge base to JSON."""
    try:
        store = get_store()
    except Exception as e:
        typer.echo(f"Knowledge store unavailable: {e}", err=True)
        raise typer.Exit(1)

    if project:
        query = "SELECT * FROM knowledge WHERE project = $project"
        params: dict[str, Any] = {"project": project}
    else:
        query = "SELECT * FROM knowledge"
        params = {}

    result = _run(store.db.query(query, params))
    rows = (
        result[0]["result"]
        if isinstance(result, list) and result and "result" in result[0]
        else result
    )
    entries = rows or []

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
        with open(output, "w") as f:
            f.write(json_str)
        typer.echo(f"Exported {len(entries)} entries to {output}")
    else:
        typer.echo(json_str)
