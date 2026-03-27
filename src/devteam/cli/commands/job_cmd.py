"""devteam job control commands — start, status, stop, pause, resume, cancel,
comment, answer.

These are registered as top-level commands (not under a subgroup) because
they are the primary operator interface.

Commands delegate to ``cli_bridge`` for in-process mode. In daemon mode
(future) they will call the daemon HTTP API instead.
"""

from __future__ import annotations

import typer

from devteam.orchestrator.cli_bridge import (
    JobStore,
    handle_answer,
    handle_cancel,
    handle_comment,
    handle_start,
    handle_status,
)

# Module-level singleton store for in-process mode.
# In production the daemon owns the store; CLI commands will call the
# daemon HTTP API.  This singleton exists so the wiring is real.
_store: JobStore | None = None


def _get_store() -> JobStore:
    """Return the module-level singleton JobStore, creating it lazily."""
    global _store
    if _store is None:
        _store = JobStore()
    return _store


def register_job_commands(app: typer.Typer) -> None:
    """Register job control commands directly on the main app."""

    @app.command()
    def start(
        spec: str | None = typer.Option(None, "--spec", help="Path to spec document"),
        plan: str | None = typer.Option(None, "--plan", help="Path to plan document"),
        prompt: str | None = typer.Option(None, "--prompt", help="Direct prompt for small fixes"),
        issue: str | None = typer.Option(None, "--issue", help="GitHub issue URL"),
        priority: str | None = typer.Option(
            None, "--priority", help="Job priority: high, normal, low"
        ),
    ) -> None:
        """Start a new development job."""
        # Validate priority early if provided
        if priority is not None:
            from devteam.concurrency.cli_priority import parse_priority_flag

            try:
                parse_priority_flag(priority)
            except ValueError as e:
                typer.echo(f"Error: {e}")
                raise typer.Exit(code=1)

        if not any([spec, plan, prompt, issue]):
            typer.echo("Provide --spec/--plan, --prompt, or --issue to start a job.")
            raise typer.Exit(code=1)

        store = _get_store()
        title = "Job from CLI"
        if prompt:
            title = prompt[:60]
        elif spec:
            title = f"Spec: {spec[:50]}"

        job = handle_start(
            store,
            title=title,
            spec=spec,
            plan=plan,
            issue=issue,
            prompt=prompt,
        )
        typer.echo(f"Job {job.id} created ({job.status.value}).")

    @app.command()
    def status(
        target: str | None = typer.Argument(
            None, help="Job ID (W-1), task (W-1/T-3), or omit for all"
        ),
        questions: bool = typer.Option(False, "--questions", help="Show pending questions"),
    ) -> None:
        """Show status of active jobs and tasks."""
        store = _get_store()

        if questions:
            pending = store.get_pending_questions(job_id=target)
            if not pending:
                typer.echo("No pending questions.")
                return
            for q in pending:
                typer.echo(f"  {q.id} ({q.job_id}/{q.task_id}): {q.record.question}")
            return

        result = handle_status(store, target)
        if "error" in result:
            typer.echo(result["error"])
            raise typer.Exit(code=1)

        if "jobs" in result:
            jobs = result["jobs"]
            if not jobs:
                typer.echo("No active jobs.")
                return
            for j in jobs:  # type: ignore[union-attr]
                prog = j.get("progress", (0, 0))
                typer.echo(
                    f"  {j['job_id']}  {j['status']:<12}  {j['title']}  [{prog[0]}/{prog[1]}]"
                )
        else:
            prog = result.get("progress", (0, 0))
            typer.echo(
                f"Job {result['job_id']}: {result['status']}  "
                f"[{prog[0]}/{prog[1]}]  {result['title']}"  # type: ignore[index]
            )

    @app.command()
    def stop(
        target: str | None = typer.Argument(None, help="Job ID (W-1) or omit for all"),
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
        target: str | None = typer.Argument(None, help="Job ID (W-1); omit to resume daemon"),
    ) -> None:
        """Resume a paused job or recover workflows after crash."""
        if target:
            typer.echo(f"Not yet implemented: resume {target}")
        else:
            typer.echo("Not yet implemented: resume daemon")

    @app.command()
    def cancel(
        target: str = typer.Argument(help="Job ID (W-1)"),
        revert_merged: bool = typer.Option(
            False, "--revert-merged", help="Create revert PRs for merged work"
        ),
    ) -> None:
        """Cancel a job and clean up all resources."""
        store = _get_store()
        job = store.get(target)
        if not job:
            typer.echo(f"Job {target} not found.")
            raise typer.Exit(code=1)
        success = handle_cancel(store, target)
        if success:
            typer.echo(f"Job {target} canceled.")
        else:
            typer.echo(f"Job {target} is already {job.status.value}.")
        if revert_merged:
            typer.echo("Not yet implemented: revert merged PRs")

    @app.command()
    def comment(
        target: str = typer.Argument(help="Task reference (W-1/T-3 or T-3)"),
        message: str = typer.Argument(help="Feedback message"),
    ) -> None:
        """Inject feedback into a running task."""
        store = _get_store()
        try:
            success = handle_comment(store, target, message)
        except ValueError as e:
            typer.echo(str(e))
            raise typer.Exit(code=1)
        if success:
            typer.echo(f"Comment added to {target}.")
        else:
            typer.echo(f"Target {target} not found.")
            raise typer.Exit(code=1)

    @app.command()
    def answer(
        question_ref: str = typer.Argument(help="Question reference (Q-1 or W-1/Q-1)"),
        response: str = typer.Argument(help="Your answer"),
    ) -> None:
        """Answer a pending question to resume a paused task."""
        store = _get_store()
        try:
            result = handle_answer(store, question_ref, response)
        except ValueError as e:
            typer.echo(f"Error: {e}")
            raise typer.Exit(code=1)
        if result is None:
            typer.echo(f"Question {question_ref} not found.")
            raise typer.Exit(code=1)
        typer.echo(
            f"Answer recorded for {question_ref}. Task will resume when the daemon processes it."
        )
