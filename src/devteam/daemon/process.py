"""Daemon process management -- start, stop, PID file, singleton lock.

The devteam daemon is a single long-running process. A PID file at
~/.devteam/daemon.pid provides singleton locking. A port file at
~/.devteam/daemon.port records the active listening port.
"""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class DaemonAlreadyRunningError(Exception):
    """Raised when trying to start a daemon that is already running."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        super().__init__(f"Daemon already running with PID {pid}")


class DaemonNotRunningError(Exception):
    """Raised when a command requires a running daemon but none is found."""

    def __init__(self) -> None:
        super().__init__("Daemon is not running. Start it with: devteam daemon start")


@dataclass
class DaemonState:
    """Current state of the daemon process."""

    running: bool
    pid: Optional[int] = None
    port: Optional[int] = None
    stale: bool = False


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def write_pid_file(pid_path: Path, pid: int) -> None:
    """Write the daemon PID to the PID file."""
    pid_path.write_text(f"{pid}\n")


def read_pid_file(pid_path: Path) -> Optional[int]:
    """Read the daemon PID from the PID file.

    Returns None if the file doesn't exist or contains invalid data.
    """
    if not pid_path.exists():
        return None
    try:
        text = pid_path.read_text().strip()
        return int(text)
    except (ValueError, OSError):
        return None


def acquire_pid_lock(pid_path: Path, new_pid: int) -> None:
    """Acquire the singleton daemon lock.

    If a PID file exists with a running process, raises DaemonAlreadyRunningError.
    If the PID file is stale (process not running), overwrites it.
    """
    existing_pid = read_pid_file(pid_path)
    if existing_pid is not None and _is_process_alive(existing_pid):
        raise DaemonAlreadyRunningError(existing_pid)

    write_pid_file(pid_path, new_pid)


def release_pid_lock(pid_path: Path) -> None:
    """Release the daemon lock by removing the PID file."""
    try:
        pid_path.unlink()
    except FileNotFoundError:
        pass


def write_port_file(port_path: Path, port: int) -> None:
    """Write the daemon port to the port file."""
    port_path.write_text(f"{port}\n")


def read_port_file(port_path: Path) -> Optional[int]:
    """Read the daemon port from the port file."""
    if not port_path.exists():
        return None
    try:
        return int(port_path.read_text().strip())
    except (ValueError, OSError):
        return None


def get_daemon_state(pid_path: Path, port_path: Path) -> DaemonState:
    """Get the current daemon state by inspecting PID and port files."""
    pid = read_pid_file(pid_path)

    if pid is None:
        return DaemonState(running=False)

    if not _is_process_alive(pid):
        return DaemonState(running=False, pid=pid, stale=True)

    port = read_port_file(port_path)
    return DaemonState(running=True, pid=pid, port=port)


def stop_daemon(pid_path: Path, port_path: Path, *, force: bool = False) -> int:
    """Stop the running daemon process.

    Returns the PID of the stopped process.
    Raises DaemonNotRunningError if no daemon is running.
    """
    pid = read_pid_file(pid_path)
    if pid is None or not _is_process_alive(pid):
        raise DaemonNotRunningError()

    sig = signal.SIGKILL if force else signal.SIGTERM
    os.kill(pid, sig)

    release_pid_lock(pid_path)
    try:
        port_path.unlink()
    except FileNotFoundError:
        pass

    return pid
