"""devteam project — add/remove project registrations."""

from __future__ import annotations

import typer

app = typer.Typer(help="Manage registered projects.")


@app.command()
def add(
    path: str = typer.Argument(help="Path to the project repository"),
) -> None:
    """Register a project repository with the daemon."""
    typer.echo(f"Not yet implemented: project add ({path})")
    raise typer.Exit(code=1)


@app.command()
def remove(
    name: str = typer.Argument(help="Project name to unregister"),
) -> None:
    """Unregister a project from the daemon."""
    typer.echo(f"Not yet implemented: project remove ({name})")
    raise typer.Exit(code=1)
