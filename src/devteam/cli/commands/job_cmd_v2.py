"""devteam job control commands — start, status, answer, resume.

V2: Uses bootstrap + RuntimeStateStore + DBOS instead of cli_bridge/JobStore.
Simple stdout streaming — no prompt_toolkit yet.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from devteam.orchestrator.runtime_state import RuntimeStateStore

logger_app = typer.Typer()


def _get_store() -> RuntimeStateStore:
    """Get or create RuntimeStateStore for read-only CLI commands."""
    db_path = Path.home() / ".devteam" / "runtime.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return RuntimeStateStore(str(db_path))


def register_job_commands_v2(app: typer.Typer) -> None:
    """Register v2 job control commands on the main app."""

    @app.command("start")
    def start(
        spec: str | None = typer.Option(None, "--spec", help="Path to spec document"),
        plan: str | None = typer.Option(None, "--plan", help="Path to plan document"),
        prompt: str | None = typer.Option(None, "--prompt", help="Direct prompt for small fixes"),
    ) -> None:
        """Start a new development job."""
        if not any([spec, plan, prompt]):
            typer.echo("Provide --spec/--plan or --prompt to start a job.")
            raise typer.Exit(code=1)

        # Read file contents if paths provided
        spec_content = ""
        plan_content = ""
        if spec:
            spec_path = Path(spec)
            if spec_path.exists():
                spec_content = spec_path.read_text()
            else:
                spec_content = spec
        if plan:
            plan_path = Path(plan)
            if plan_path.exists():
                plan_content = plan_path.read_text()
            else:
                plan_content = plan
        if prompt:
            spec_content = prompt

        async def _run() -> tuple:
            from devteam.orchestrator.bootstrap import bootstrap

            return await bootstrap(spec=spec_content, plan=plan_content)

        try:
            handle, alias = asyncio.run(_run())
            typer.echo(f"Job {alias} started (workflow: {handle.workflow_id}).")
            typer.echo("Use 'devteam status' to monitor progress.")
        except Exception as e:
            typer.echo(f"Error: {e}")
            raise typer.Exit(code=1)

    @app.command("status")
    def status(
        target: str | None = typer.Argument(
            None, help="Job alias (W-1) or omit for all active jobs"
        ),
        questions: bool = typer.Option(False, "--questions", help="Show pending questions"),
    ) -> None:
        """Show status of active jobs and tasks."""
        try:
            store = _get_store()
            try:
                if questions:
                    pending = store.get_pending_questions(job_alias=target)
                    if not pending:
                        typer.echo("No pending questions.")
                        return
                    for q in pending:
                        marker = "RESOLVED" if q.resolved else "PENDING"
                        typer.echo(f"  {q.display_alias} [{marker}] ({q.task_alias}): {q.text}")
                    return

                if target:
                    job = store.get_job(target)
                    if not job:
                        typer.echo(f"Job {target} not found.")
                        raise typer.Exit(code=1)
                    tasks = store.get_tasks_for_job(target)
                    typer.echo(f"Job {job.alias}: {job.status}")
                    typer.echo(f"  Project: {job.project_name}")
                    for t in tasks:
                        typer.echo(f"  {t.alias}: {t.status} ({t.assigned_to})")
                else:
                    active = store.get_active_jobs()
                    if not active:
                        typer.echo("No active jobs.")
                        return
                    for j in active:
                        tasks = store.get_tasks_for_job(j.alias)
                        done = sum(1 for t in tasks if t.status in ("completed", "failed"))
                        typer.echo(f"  {j.alias}: {j.status} [{done}/{len(tasks)} tasks]")
            finally:
                store.close()
        except typer.Exit:
            raise
        except Exception as e:
            typer.echo(f"Error: {e}")
            raise typer.Exit(code=1)

    @app.command("answer")
    def answer(
        question_ref: str = typer.Argument(help="Question alias (Q-1)"),
        response: str = typer.Argument(help="Your answer"),
    ) -> None:
        """Answer a pending question to unblock a task."""
        try:
            store = _get_store()
        except Exception as e:
            typer.echo(f"Error: {e}")
            raise typer.Exit(code=1)
        try:
            q = store.lookup_question(question_ref)
            if q is None:
                typer.echo(f"Question {question_ref} not found.")
                raise typer.Exit(code=1)
            if q.resolved:
                typer.echo(f"Question {question_ref} is already resolved.")
                return

            # Send answer to the waiting workflow via DBOS
            async def _send() -> None:
                from dbos import DBOS

                devteam_dir = Path.home() / ".devteam"
                db_path = f"sqlite:///{devteam_dir / 'devteam_system.sqlite'}"
                DBOS(config={"name": "devteam", "system_database_url": db_path})
                DBOS.launch()
                try:
                    # internal_id format: "Q-{task_alias}-{n}" e.g. "Q-T-1-1"
                    # Extract question number from the end
                    q_num = q.internal_id.rsplit("-", 1)[-1] if "-" in q.internal_id else "1"
                    topic = f"answer:{q.task_alias}-Q{q_num}"
                    await DBOS.send_async(q.child_workflow_id, response, topic=topic)
                finally:
                    DBOS.destroy()

            asyncio.run(_send())
            store.resolve_question(question_ref)
            typer.echo(f"Answer sent for {question_ref}. Task will resume.")
        finally:
            store.close()

    @app.command("resume")
    def resume(
        target: str | None = typer.Argument(None, help="Job alias (W-1) or omit to recover all"),
    ) -> None:
        """Resume workflows after a crash or restart."""

        async def _resume() -> None:
            from dbos import DBOS

            devteam_dir = Path.home() / ".devteam"
            devteam_dir.mkdir(parents=True, exist_ok=True)
            db_path = f"sqlite:///{devteam_dir / 'devteam_system.sqlite'}"

            DBOS(config={"name": "devteam", "system_database_url": db_path})
            DBOS.launch()
            # Note: DBOS stays alive intentionally — it recovers and runs
            # pending workflows in the background. destroy() is called on process exit.
            typer.echo("DBOS launched — recovering workflows...")

        try:
            asyncio.run(_resume())
        except Exception as e:
            typer.echo(f"Resume failed: {e}")
            raise typer.Exit(code=1)

        store = _get_store()
        try:
            active = store.get_active_jobs()
            if target:
                job = store.get_job(target)
                if job:
                    typer.echo(f"Job {job.alias} ({job.status}) — workflows recovering.")
                else:
                    typer.echo(f"Job {target} not found in runtime state.")
            elif active:
                for j in active:
                    typer.echo(f"  {j.alias}: {j.status}")
            else:
                typer.echo("No active jobs to resume.")
        finally:
            store.close()
