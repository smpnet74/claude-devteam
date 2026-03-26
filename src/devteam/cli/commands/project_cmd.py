"""devteam project — add/remove project registrations."""

from __future__ import annotations

from pathlib import Path

import typer

from devteam.agents.template_manager import copy_agents_to_project
from devteam.cli.common import get_devteam_home

app = typer.Typer(help="Manage registered projects.")


@app.command()
def add(
    path: str = typer.Argument(help="Path to the project repository"),
) -> None:
    """Register a project repository with the daemon."""
    project_dir = Path(path).resolve()
    if not project_dir.is_dir():
        typer.echo(f"Error: project directory not found: {project_dir}")
        raise typer.Exit(code=1)

    home = get_devteam_home()
    global_agents_dir = home / "agents"

    if not global_agents_dir.is_dir():
        typer.echo("Error: run 'devteam init' first to set up agent templates.")
        raise typer.Exit(code=1)

    try:
        copied = copy_agents_to_project(global_agents_dir, project_dir, overwrite=False)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}")
        raise typer.Exit(code=1)
    claude_agents = project_dir / ".claude" / "agents"
    typer.echo(f"Copied {len(copied)} agent definitions to {claude_agents}")


@app.command()
def remove(
    name: str = typer.Argument(help="Project name to unregister"),
) -> None:
    """Unregister a project from the daemon."""
    typer.echo(f"Not yet implemented: project remove ({name})")
