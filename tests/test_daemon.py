"""Tests for daemon process management."""

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from devteam.daemon.process import (
    DaemonAlreadyRunningError,
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
