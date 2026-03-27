"""devteam prioritize — change priority of a queued task.

Wired into main.py as a top-level command.
"""

from __future__ import annotations

import typer

from devteam.concurrency.cli_priority import parse_priority_flag


def register_concurrency_commands(app: typer.Typer) -> None:
    """Register concurrency-related commands on the main app."""

    @app.command()
    def prioritize(
        job_id: str = typer.Argument(help="Job ID (e.g., W-1)"),
        task_id: str = typer.Argument(help="Task ID (e.g., T-3)"),
        level: str = typer.Argument(help="Priority level: high, normal, low"),
    ) -> None:
        """Change the priority of a queued task."""
        try:
            priority = parse_priority_flag(level)
        except ValueError as e:
            typer.echo(f"Error: {e}")
            raise typer.Exit(code=1)

        typer.echo(
            "Error: 'prioritize' requires a running daemon (not yet implemented).",
            err=True,
        )
        raise typer.Exit(code=1)
