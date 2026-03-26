"""Tests for daemon process management."""

import os
from pathlib import Path

import pytest

from devteam.daemon.process import (
    DaemonAlreadyRunningError,
    DaemonNotRunningError,
    DaemonState,
    acquire_pid_lock,
    get_daemon_state,
    read_pid_file,
    release_pid_lock,
    write_pid_file,
)


class TestPIDFile:
    def test_write_and_read_pid_file(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        write_pid_file(pid_path, 12345)
        assert read_pid_file(pid_path) == 12345

    def test_read_missing_pid_file(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        assert read_pid_file(pid_path) is None

    def test_read_corrupt_pid_file(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        pid_path.write_text("not-a-number\n")
        assert read_pid_file(pid_path) is None


class TestPIDLock:
    def test_acquire_lock_succeeds(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        acquire_pid_lock(pid_path, os.getpid())
        assert read_pid_file(pid_path) == os.getpid()

    def test_acquire_lock_fails_if_running(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        # Write our own PID (which is a running process)
        write_pid_file(pid_path, os.getpid())
        with pytest.raises(DaemonAlreadyRunningError):
            acquire_pid_lock(pid_path, 99999)

    def test_acquire_lock_succeeds_if_stale(self, tmp_devteam_home: Path) -> None:
        """If the PID in the file is not a running process, the lock is stale."""
        pid_path = tmp_devteam_home / "daemon.pid"
        # Use a PID that almost certainly doesn't exist
        write_pid_file(pid_path, 4_000_000)
        acquire_pid_lock(pid_path, os.getpid())
        assert read_pid_file(pid_path) == os.getpid()

    def test_release_lock(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        write_pid_file(pid_path, os.getpid())
        release_pid_lock(pid_path)
        assert not pid_path.exists()

    def test_release_missing_lock_is_noop(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        release_pid_lock(pid_path)  # Should not raise


class TestDaemonState:
    def test_state_stopped_when_no_pid(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        port_path = tmp_devteam_home / "daemon.port"
        state = get_daemon_state(pid_path, port_path)
        assert state.running is False
        assert state.pid is None

    def test_state_running_with_pid(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        port_path = tmp_devteam_home / "daemon.port"
        write_pid_file(pid_path, os.getpid())
        port_path.write_text("7432\n")
        state = get_daemon_state(pid_path, port_path)
        assert state.running is True
        assert state.pid == os.getpid()
        assert state.port == 7432

    def test_state_stale_pid(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        port_path = tmp_devteam_home / "daemon.port"
        write_pid_file(pid_path, 4_000_000)
        state = get_daemon_state(pid_path, port_path)
        assert state.running is False
        assert state.stale is True
