"""Tests for daemon process management."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from devteam.daemon.process import (
    DaemonAlreadyRunningError,
    _cleanup_if_owner,
    _release_stale_lock,
    acquire_pid_lock,
    get_daemon_state,
    read_pid_file,
    read_port_file,
    release_pid_lock,
    write_pid_file,
    write_port_file,
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

    def test_read_pid_file_negative(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        pid_path.write_text("-5\n")
        assert read_pid_file(pid_path) is None


class TestPortFile:
    def test_write_and_read_port_file(self, tmp_devteam_home: Path) -> None:
        port_path = tmp_devteam_home / "daemon.port"
        write_port_file(port_path, 7432)
        assert read_port_file(port_path) == 7432

    def test_read_port_file_missing(self, tmp_devteam_home: Path) -> None:
        port_path = tmp_devteam_home / "daemon.port"
        assert read_port_file(port_path) is None

    def test_read_port_file_out_of_range(self, tmp_devteam_home: Path) -> None:
        port_path = tmp_devteam_home / "daemon.port"
        port_path.write_text("99999\n")
        assert read_port_file(port_path) is None

    def test_read_port_file_negative(self, tmp_devteam_home: Path) -> None:
        port_path = tmp_devteam_home / "daemon.port"
        port_path.write_text("-1\n")
        assert read_port_file(port_path) is None


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
        write_pid_file(pid_path, 99999)
        with patch("devteam.daemon.process._is_process_alive", return_value=False):
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
        write_pid_file(pid_path, 99999)
        with patch("devteam.daemon.process._is_process_alive", return_value=False):
            state = get_daemon_state(pid_path, port_path)
        assert state.running is False
        assert state.stale is True

    def test_state_running_without_port_file(self, tmp_devteam_home: Path) -> None:
        """Running daemon with missing port file returns port=None."""
        pid_path = tmp_devteam_home / "daemon.pid"
        port_path = tmp_devteam_home / "daemon.port"
        write_pid_file(pid_path, os.getpid())
        # port_path intentionally not created
        state = get_daemon_state(pid_path, port_path)
        assert state.running is True
        assert state.pid == os.getpid()
        assert state.port is None


class TestDaemonServer:
    @pytest.fixture
    def app(self) -> FastAPI:
        from devteam.daemon.server import create_app

        return create_app()

    @pytest.fixture
    async def client(self, app: FastAPI) -> AsyncClient:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    @pytest.mark.asyncio
    async def test_status_endpoint(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data

    @pytest.mark.asyncio
    async def test_start_job_stub(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/jobs",
            json={"title": "Test Job", "spec_path": "/tmp/spec.md"},
        )
        assert resp.status_code == 501
        assert "not yet implemented" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_stop_job_stub(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/jobs/W-1/stop")
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_pause_job_stub(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/jobs/W-1/pause")
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_resume_job_stub(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/jobs/W-1/resume")
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_cancel_job_stub(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/jobs/W-1/cancel")
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_answer_question_stub(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/jobs/W-1/questions/Q-1/answer",
            json={"answer": "Use JWT"},
        )
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_focus_stub(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/focus",
            json={"job_id": "W-1", "shell_pid": 12345},
        )
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_project_add_stub(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/projects",
            json={"path": "/path/to/repo"},
        )
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_project_remove_stub(self, client: AsyncClient) -> None:
        resp = await client.delete("/api/v1/projects/myapp")
        assert resp.status_code == 501

    # --- Path parameter validation (finding 3) ---

    @pytest.mark.asyncio
    async def test_get_job_invalid_id(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/jobs/bad-id")
        assert resp.status_code == 422
        assert "Invalid job ID format" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_stop_job_invalid_id(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/jobs/bad-id/stop")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_pause_job_invalid_id(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/jobs/bad-id/pause")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_resume_job_invalid_id(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/jobs/bad-id/resume")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_cancel_job_invalid_id(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/jobs/bad-id/cancel")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_answer_question_invalid_job_id(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/jobs/bad-id/questions/Q-1/answer",
            json={"answer": "yes"},
        )
        assert resp.status_code == 422
        assert "Invalid job ID format" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_answer_question_invalid_question_id(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/jobs/W-1/questions/bad-id/answer",
            json={"answer": "yes"},
        )
        assert resp.status_code == 422
        assert "Invalid question ID format" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_valid_ids_reach_stub(self, client: AsyncClient) -> None:
        """Valid IDs should pass validation and hit the 501 stub."""
        resp = await client.get("/api/v1/jobs/W-42")
        assert resp.status_code == 501


class TestCleanupIfOwnerRace:
    """Regression tests for _cleanup_if_owner race condition (finding 8)."""

    def test_cleanup_skips_when_pid_mismatch(self, tmp_devteam_home: Path) -> None:
        """_cleanup_if_owner must NOT delete files when another daemon owns them."""
        pid_path = tmp_devteam_home / "daemon.pid"
        port_path = tmp_devteam_home / "daemon.port"
        # Another daemon (PID 99999) owns the files
        write_pid_file(pid_path, 99999)
        write_port_file(port_path, 7432)
        # We (PID 11111) try to clean up -- should be a no-op
        _cleanup_if_owner(pid_path, port_path, 11111)
        # Both files must still exist
        assert pid_path.exists()
        assert port_path.exists()
        assert read_pid_file(pid_path) == 99999

    def test_cleanup_removes_when_pid_matches(self, tmp_devteam_home: Path) -> None:
        """_cleanup_if_owner removes both files when PID matches."""
        pid_path = tmp_devteam_home / "daemon.pid"
        port_path = tmp_devteam_home / "daemon.port"
        write_pid_file(pid_path, 12345)
        write_port_file(port_path, 7432)
        _cleanup_if_owner(pid_path, port_path, 12345)
        assert not pid_path.exists()
        assert not port_path.exists()


class TestReleaseStaleLockRace:
    """Regression tests for _release_stale_lock race condition (finding 8)."""

    def test_release_stale_lock_succeeds_when_pid_matches(self, tmp_devteam_home: Path) -> None:
        pid_path = tmp_devteam_home / "daemon.pid"
        write_pid_file(pid_path, 99999)
        assert _release_stale_lock(pid_path, 99999) is True
        assert not pid_path.exists()

    def test_release_stale_lock_refuses_when_pid_replaced(self, tmp_devteam_home: Path) -> None:
        """If another process replaced the lock, _release_stale_lock must not delete."""
        pid_path = tmp_devteam_home / "daemon.pid"
        # Lock was replaced by a new daemon (PID 88888)
        write_pid_file(pid_path, 88888)
        # We still think the stale PID was 99999
        assert _release_stale_lock(pid_path, 99999) is False
        # The new daemon's lock file must still exist
        assert pid_path.exists()
        assert read_pid_file(pid_path) == 88888
