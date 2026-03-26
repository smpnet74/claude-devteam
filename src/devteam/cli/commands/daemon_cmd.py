"""devteam daemon — start/stop/status for the daemon process."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import typer

from devteam.cli.common import get_devteam_home
from devteam.daemon.process import (
    DaemonAlreadyRunningError,
    DaemonNotRunningError,
    get_daemon_state,
    stop_daemon,
)

app = typer.Typer(help="Manage the devteam daemon process.")


@app.command()
def start(
    port: int = typer.Option(7432, help="Port to listen on"),
    foreground: bool = typer.Option(False, "--foreground", "-f", help="Run in foreground"),
) -> None:
    """Start the devteam daemon."""
    if port <= 0 or port > 65535:
        typer.echo("Error: port must be between 1 and 65535")
        raise typer.Exit(code=1)

    home = get_devteam_home()
    pid_path = home / "daemon.pid"
    port_path = home / "daemon.port"

    if not home.exists():
        typer.echo("Error: devteam not initialized. Run 'devteam init' first.")
        raise typer.Exit(code=1)

    state = get_daemon_state(pid_path, port_path)
    if state.running:
        typer.echo(f"Daemon already running (PID {state.pid}, port {state.port})")
        raise typer.Exit(code=0)

    if foreground:
        from devteam.daemon.process import acquire_pid_lock, write_port_file
        from devteam.daemon.server import create_app

        import uvicorn

        try:
            acquire_pid_lock(pid_path, os.getpid())
        except DaemonAlreadyRunningError as e:
            state = get_daemon_state(pid_path, port_path)
            typer.echo(f"Daemon already running (PID {state.pid or e.pid}, port {state.port})")
            raise typer.Exit(code=0)
        except (OSError, RuntimeError) as e:
            typer.echo(f"Error: failed to acquire daemon lock: {e}")
            raise typer.Exit(code=1)
        typer.echo(f"Starting daemon on port {port} (foreground, PID {os.getpid()})")
        try:
            write_port_file(port_path, port)
            app_instance = create_app()
            uvicorn.run(app_instance, host="127.0.0.1", port=port, log_level="warning")
        finally:
            from devteam.daemon.process import _cleanup_if_owner

            _cleanup_if_owner(pid_path, port_path, os.getpid())
    else:
        cmd = [
            sys.executable,
            "-m",
            "devteam.cli.commands.daemon_cmd",
            "--port",
            str(port),
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            typer.echo(f"Error: failed to start daemon process: {e}")
            raise typer.Exit(code=1)
        time.sleep(0.5)
        state = get_daemon_state(pid_path, port_path)
        if state.running:
            typer.echo(f"Daemon started (PID {state.pid}, port {port})")
        else:
            typer.echo(f"Warning: daemon process {proc.pid} may not have started correctly")
            raise typer.Exit(code=1)


@app.command()
def stop(
    force: bool = typer.Option(False, "--force", help="Force kill"),
) -> None:
    """Stop the devteam daemon."""
    home = get_devteam_home()
    pid_path = home / "daemon.pid"
    port_path = home / "daemon.port"

    try:
        pid = stop_daemon(pid_path, port_path, force=force)
        typer.echo(f"Daemon stopped (PID {pid})")
    except DaemonNotRunningError:
        typer.echo("Daemon is not running.")
        raise typer.Exit(code=1)
    except (OSError, RuntimeError) as e:
        typer.echo(f"Error: failed to stop daemon: {e}")
        raise typer.Exit(code=1)


@app.command()
def status() -> None:
    """Show daemon status."""
    home = get_devteam_home()
    pid_path = home / "daemon.pid"
    port_path = home / "daemon.port"

    state = get_daemon_state(pid_path, port_path)

    if state.running:
        typer.echo(f"Daemon is running (PID {state.pid}, port {state.port})")
    elif state.stale:
        typer.echo(f"Daemon is not running (stale PID file: {state.pid})")
    else:
        typer.echo("Daemon is not running.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7432)
    args = parser.parse_args()
    start(port=args.port, foreground=True)
