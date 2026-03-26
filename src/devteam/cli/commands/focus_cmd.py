"""devteam focus — set/clear the focused job for the current shell."""

from __future__ import annotations

import typer

app = typer.Typer(help="Set or show the focused job for this shell.")


@app.callback(invoke_without_command=True)
def focus(
    job_id: str | None = typer.Argument(None, help="Job ID to focus (W-1)"),
    clear: bool = typer.Option(False, "--clear", help="Clear focus"),
) -> None:
    """Set, show, or clear the focused job for this shell session."""
    if clear:
        typer.echo("Not yet implemented: clear focus")
    elif job_id:
        typer.echo(f"Not yet implemented: focus on {job_id}")
    else:
        typer.echo("Not yet implemented: show current focus")
