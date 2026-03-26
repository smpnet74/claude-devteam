"""Typer CLI entry point for devteam."""

import typer

from devteam.cli.commands import daemon_cmd, init_cmd, project_cmd

app = typer.Typer(
    name="devteam",
    help="Durable AI Development Team Orchestrator",
    no_args_is_help=True,
)

# Register command groups
app.add_typer(init_cmd.app, name="init")
app.add_typer(daemon_cmd.app, name="daemon")
app.add_typer(project_cmd.app, name="project")


def main() -> None:
    app()
