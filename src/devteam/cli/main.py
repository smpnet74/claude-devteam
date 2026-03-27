"""Typer CLI entry point for devteam."""

import typer

from devteam.cli.commands import daemon_cmd, focus_cmd, init_cmd, project_cmd
from devteam.cli.commands.concurrency_cmd import register_concurrency_commands
from devteam.cli.commands.git_commands import git_app
from devteam.cli.commands.job_cmd import register_job_commands
from devteam.cli.commands.knowledge_cmd import knowledge_app

app = typer.Typer(
    name="devteam",
    help="Durable AI Development Team Orchestrator",
    no_args_is_help=True,
)

# Register command groups
app.add_typer(init_cmd.app, name="init")
app.add_typer(daemon_cmd.app, name="daemon")
app.add_typer(project_cmd.app, name="project")
app.add_typer(focus_cmd.app, name="focus")
app.add_typer(git_app, name="git")
app.add_typer(knowledge_app, name="knowledge")

# Register top-level job control commands
register_job_commands(app)

# Register concurrency commands (prioritize)
register_concurrency_commands(app)


def main() -> None:
    app()
