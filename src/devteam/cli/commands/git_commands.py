"""Git lifecycle CLI commands -- cancel, merge, takeover, handback.

These commands send requests to the daemon, which performs the actual
git/GitHub operations. The CLI only formats output.
"""

from __future__ import annotations

from typing import Any

import typer

git_app = typer.Typer(name="git", help="Git lifecycle commands")


def send_cancel_request(job_id: str) -> dict[str, Any]:
    """Send a cancel request to the daemon. Placeholder for httpx call."""
    raise NotImplementedError("Daemon integration in Plan 1")


def send_merge_request(pr_ref: str) -> dict[str, Any]:
    """Send a merge request to the daemon. Placeholder for httpx call."""
    raise NotImplementedError("Daemon integration in Plan 1")


def send_takeover_request(task_ref: str) -> dict[str, Any]:
    """Send a takeover request to the daemon. Placeholder for httpx call."""
    raise NotImplementedError("Daemon integration in Plan 1")


def send_handback_request(task_ref: str) -> dict[str, Any]:
    """Send a handback request to the daemon. Placeholder for httpx call."""
    raise NotImplementedError("Daemon integration in Plan 1")


@git_app.command("cancel")
def cancel_command(
    job_id: str = typer.Argument(help="Job ID to cancel (e.g. W-1)"),
    revert_merged: bool = typer.Option(
        False, "--revert-merged", help="Create revert PRs for merged work"
    ),
) -> None:
    """Cancel a job and clean up all worktrees, branches, and PRs."""
    result = send_cancel_request(job_id)

    if not result.get("success"):
        typer.echo(f"Error: {result.get('error', 'Unknown error')}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"[{job_id}] CANCELLED\n")

    cleaned = result.get("cleaned", [])
    if cleaned:
        typer.echo("  Cleaned up:")
        for item in cleaned:
            action = item.get("action", "")
            branch = item.get("branch", "")
            pr = item.get("pr_number", "")
            if pr:
                typer.echo(f"    Closed PR #{pr} {branch}")
            else:
                typer.echo(f"    {action} {branch}")

    preserved = result.get("preserved", [])
    if preserved:
        typer.echo("\n  Preserved (already merged):")
        for item in preserved:
            pr = item.get("pr_number", "")
            branch = item.get("branch", "")
            typer.echo(f"    PR #{pr} {branch} -- merged before cancel")


@git_app.command("merge")
def merge_command(
    pr_ref: str = typer.Argument(help="PR reference (e.g. W-1/PR-42)"),
) -> None:
    """Manually merge a PR (only when merge=manual in config).

    Verifies all checks passed before merging. Will not force-merge
    a failing PR.
    """
    result = send_merge_request(pr_ref)

    if not result.get("success"):
        error = result.get("error", "Unknown error")
        typer.echo(f"Error: {error}", err=True)
        failed = result.get("failed_checks", [])
        if failed:
            typer.echo("  Failed checks:", err=True)
            for check in failed:
                typer.echo(f"    - {check}", err=True)
        raise typer.Exit(code=1)

    pr_number = result.get("pr_number", "?")
    typer.echo(f"PR #{pr_number} merged successfully.")


@git_app.command("takeover")
def takeover_command(
    task_ref: str = typer.Argument(help="Task reference (e.g. W-1/T-3)"),
) -> None:
    """Pause a task and get the worktree path for manual editing.

    After editing, commit your changes and run ``devteam handback``.
    """
    result = send_takeover_request(task_ref)

    if not result.get("success"):
        typer.echo(f"Error: {result.get('error', 'Unknown error')}", err=True)
        raise typer.Exit(code=1)

    worktree = result.get("worktree_path", "")
    task_id = result.get("task_id", task_ref)
    typer.echo(f"Task {task_id} paused for manual editing.\n")
    typer.echo(f"Worktree: {worktree}")
    typer.echo("\nWhen done, commit your changes and run:")
    typer.echo(f"  devteam handback {task_ref}")


@git_app.command("handback")
def handback_command(
    task_ref: str = typer.Argument(help="Task reference (e.g. W-1/T-3)"),
) -> None:
    """Resume a task after manual editing.

    Runs validation checks before resuming:
    - Worktree must have a clean working tree
    - No force pushes detected
    - Changed files within expected scope
    """
    result = send_handback_request(task_ref)

    if not result.get("success"):
        error = result.get("error", "Unknown error")
        typer.echo(f"Handback failed: {error}", err=True)
        validation = result.get("validation", {})
        if not validation.get("clean", True):
            typer.echo("  Worktree has uncommitted changes. Commit first.", err=True)
        if not validation.get("scope_ok", True):
            typer.echo(
                "  Warning: files outside expected scope were modified.",
                err=True,
            )
        raise typer.Exit(code=1)

    typer.echo(f"Task {task_ref} resumed. Entering review stage.")
