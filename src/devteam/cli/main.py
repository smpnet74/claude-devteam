"""Typer CLI entry point for devteam."""

import typer

app = typer.Typer(
    name="devteam",
    help="Durable AI Development Team Orchestrator",
    no_args_is_help=True,
)


@app.callback()
def callback() -> None:
    """Durable AI Development Team Orchestrator."""


def main() -> None:
    app()
