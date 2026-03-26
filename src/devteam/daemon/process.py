"""Daemon process management -- start, stop, PID file, singleton lock.

The devteam daemon is a single long-running process. A PID file at
~/.devteam/daemon.pid provides singleton locking. A port file at
~/.devteam/daemon.port records the active listening port.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path


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
    pid: int | None = None
    port: int | None = None
    stale: bool = False


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Process exists but we can't signal it


def write_pid_file(pid_path: Path, pid: int) -> None:
    """Write the daemon PID to the PID file."""
    pid_path.write_text(f"{pid}\n")


def read_pid_file(pid_path: Path) -> int | None:
    """Read the daemon PID from the PID file.

    Returns None if the file doesn't exist or contains invalid data.
    """
    if not pid_path.exists():
        return None
    try:
        text = pid_path.read_text().strip()
        pid = int(text)
        if pid <= 0:
            return None
        return pid
    except (ValueError, OSError):
        return None


def acquire_pid_lock(pid_path: Path, new_pid: int) -> None:
    """Acquire the singleton daemon lock atomically using O_EXCL.

    If a PID file exists with a running process, raises DaemonAlreadyRunningError.
    If the PID file is stale (process not running), removes it and retries once.
    """
    for _attempt in range(2):
        try:
            fd = os.open(str(pid_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{new_pid}\n".encode())
            finally:
                os.close(fd)
            return
        except FileExistsError:
            existing_pid = read_pid_file(pid_path)
            if existing_pid is not None and _is_process_alive(existing_pid):
                raise DaemonAlreadyRunningError(existing_pid)
            # Stale lock -- remove and retry
            release_pid_lock(pid_path)

    # Should not reach here, but guard against infinite loop
    raise RuntimeError("Failed to acquire PID lock after stale recovery")


def release_pid_lock(pid_path: Path) -> None:
    """Release the daemon lock by removing the PID file."""
    try:
        pid_path.unlink()
    except FileNotFoundError:
        pass


def _cleanup_if_owner(pid_path: Path, port_path: Path, expected_pid: int) -> None:
    """Remove PID and port files only if the PID file still contains expected_pid.

    This guards against a race where a new daemon starts between our process
    dying and our cleanup running — we must not delete the new daemon's files.
    """
    current_pid = read_pid_file(pid_path)
    if current_pid == expected_pid:
        release_pid_lock(pid_path)
    try:
        port_path.unlink()
    except FileNotFoundError:
        pass


def write_port_file(port_path: Path, port: int) -> None:
    """Write the daemon port to the port file."""
    port_path.write_text(f"{port}\n")


def read_port_file(port_path: Path) -> int | None:
    """Read the daemon port from the port file."""
    if not port_path.exists():
        return None
    try:
        port = int(port_path.read_text().strip())
        if port <= 0 or port > 65535:
            return None
        return port
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


_STOP_TIMEOUT_SECONDS = 5
_STOP_POLL_INTERVAL = 0.1


def stop_daemon(pid_path: Path, port_path: Path, *, force: bool = False) -> int:
    """Stop the running daemon process.

    Sends SIGTERM (or SIGKILL with force=True), waits for the process to exit,
    then cleans up PID and port files.  If SIGTERM fails to stop the process,
    escalates to SIGKILL.  Raises RuntimeError if the process cannot be killed.

    Returns the PID of the stopped process.
    Raises DaemonNotRunningError if no daemon is running.
    """
    pid = read_pid_file(pid_path)
    if pid is None:
        raise DaemonNotRunningError()
    if not _is_process_alive(pid):
        # Process already dead — clean up stale files
        _cleanup_if_owner(pid_path, port_path, pid)
        raise DaemonNotRunningError()

    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        # Process exited between our alive-check and the kill call
        _cleanup_if_owner(pid_path, port_path, pid)
        return pid

    # Wait for process to exit before cleaning up files
    deadline = time.monotonic() + _STOP_TIMEOUT_SECONDS
    while _is_process_alive(pid) and time.monotonic() < deadline:
        time.sleep(_STOP_POLL_INTERVAL)

    # If still alive after SIGTERM, escalate to SIGKILL
    if _is_process_alive(pid) and not force:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # Process already dead
        deadline = time.monotonic() + _STOP_TIMEOUT_SECONDS
        while _is_process_alive(pid) and time.monotonic() < deadline:
            time.sleep(_STOP_POLL_INTERVAL)

    if _is_process_alive(pid):
        raise RuntimeError(f"Failed to stop daemon process {pid}")

    # Only clean up files after the process is confirmed dead.
    # Verify the PID file still belongs to us — a concurrent `daemon start`
    # could have replaced it with a new daemon's PID.
    _cleanup_if_owner(pid_path, port_path, pid)

    return pid
