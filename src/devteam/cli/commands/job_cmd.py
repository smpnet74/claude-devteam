"""devteam job control commands — start, status, stop, pause, resume, cancel.

These are registered as top-level commands (not under a subgroup) because
they are the primary operator interface.
"""

from __future__ import annotations

from typing import Optional

import typer


def register_job_commands(app: typer.Typer) -> None:
    """Register job control commands directly on the main app."""

    @app.command()
    def start(
        spec: Optional[str] = typer.Option(None, "--spec", help="Path to spec document"),
        plan: Optional[str] = typer.Option(None, "--plan", help="Path to plan document"),
        prompt: Optional[str] = typer.Option(
            None, "--prompt", help="Direct prompt for small fixes"
        ),
        issue: Optional[str] = typer.Option(None, "--issue", help="GitHub issue URL"),
        priority: str = typer.Option("normal", "--priority", help="Job priority: high/normal/low"),
    ) -> None:
        """Start a new development job."""
        if spec:
            typer.echo(f"Not yet implemented: start job from spec ({spec})")
        elif plan:
            typer.echo(f"Not yet implemented: start job from plan ({plan})")
        elif prompt:
            typer.echo("Not yet implemented: start job from prompt")
        elif issue:
            typer.echo(f"Not yet implemented: start job from issue ({issue})")
        else:
            typer.echo("Provide --spec/--plan, --prompt, or --issue to start a job.")
            raise typer.Exit(code=1)

    @app.command()
    def status(
        target: Optional[str] = typer.Argument(
            None, help="Job ID (W-1), task (W-1/T-3), or omit for all"
        ),
        questions: bool = typer.Option(False, "--questions", help="Show pending questions"),
    ) -> None:
        """Show status of active jobs and tasks."""
        if target:
            typer.echo(f"Not yet implemented: status for {target}")
        elif questions:
            typer.echo("Not yet implemented: pending questions")
        else:
            typer.echo("No active jobs.")

    @app.command()
    def stop(
        target: Optional[str] = typer.Argument(None, help="Job ID (W-1) or omit for all"),
        force: bool = typer.Option(False, "--force", help="Force kill all agents"),
    ) -> None:
        """Stop active jobs gracefully."""
        if target:
            typer.echo(f"Not yet implemented: stop job {target}")
        else:
            typer.echo("Not yet implemented: stop all jobs")

    @app.command()
    def pause(
        target: str = typer.Argument(help="Job ID (W-1)"),
    ) -> None:
        """Pause a running job."""
        typer.echo(f"Not yet implemented: pause {target}")

    @app.command()
    def resume(
        target: str = typer.Argument(help="Job ID (W-1) or omit to resume daemon"),
    ) -> None:
        """Resume a paused job or recover workflows after crash."""
        typer.echo(f"Not yet implemented: resume {target}")

    @app.command()
    def cancel(
        target: str = typer.Argument(help="Job ID (W-1)"),
        revert_merged: bool = typer.Option(
            False, "--revert-merged", help="Create revert PRs for merged work"
        ),
    ) -> None:
        """Cancel a job and clean up all resources."""
        if revert_merged:
            typer.echo(f"Not yet implemented: cancel {target} with revert")
        else:
            typer.echo(f"Not yet implemented: cancel {target}")
