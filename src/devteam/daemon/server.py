"""FastAPI daemon server.

The devteam daemon runs on localhost:7432 and provides the HTTP API
that the CLI communicates with. All job orchestration, state management,
and agent invocation flows through this server.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import devteam


def _not_implemented(feature: str) -> HTTPException:
    """Return a 501 for stub endpoints."""
    return HTTPException(status_code=501, detail=f"Not yet implemented: {feature}")


# --- Request Models ---


class StartJobRequest(BaseModel):
    title: str
    spec_path: str | None = None
    plan_path: str | None = None
    prompt: str | None = None
    issue_url: str | None = None
    priority: str = "normal"


class AnswerRequest(BaseModel):
    answer: str


class FocusRequest(BaseModel):
    job_id: str
    shell_pid: int


class ProjectAddRequest(BaseModel):
    path: str


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="devteam daemon",
        description="Durable AI Development Team Orchestrator",
        version=devteam.__version__,
    )

    # --- Health ---

    @app.get("/health")
    async def health_check() -> dict:
        return {"status": "ok", "version": devteam.__version__}

    # --- Status ---

    @app.get("/api/v1/status")
    async def get_status() -> dict:
        """Return overall daemon status and active jobs."""
        return {"jobs": [], "agents_running": 0, "rate_limited": False}

    # --- Job Management (stubs) ---

    @app.post("/api/v1/jobs")
    async def start_job(request: StartJobRequest) -> dict:
        raise _not_implemented("job creation and workflow execution")

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        raise _not_implemented("job detail retrieval")

    @app.post("/api/v1/jobs/{job_id}/stop")
    async def stop_job(job_id: str, force: bool = False) -> dict:
        raise _not_implemented("job stop")

    @app.post("/api/v1/jobs/{job_id}/pause")
    async def pause_job(job_id: str) -> dict:
        raise _not_implemented("job pause")

    @app.post("/api/v1/jobs/{job_id}/resume")
    async def resume_job(job_id: str) -> dict:
        raise _not_implemented("job resume")

    @app.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, revert_merged: bool = False) -> dict:
        raise _not_implemented("job cancellation")

    # --- Questions (stubs) ---

    @app.post("/api/v1/jobs/{job_id}/questions/{question_id}/answer")
    async def answer_question(job_id: str, question_id: str, request: AnswerRequest) -> dict:
        raise _not_implemented("question answering")

    # --- Focus (stubs) ---

    @app.post("/api/v1/focus")
    async def set_focus(request: FocusRequest) -> dict:
        raise _not_implemented("focus management")

    # --- Project Management (stubs) ---

    @app.post("/api/v1/projects")
    async def add_project(request: ProjectAddRequest) -> dict:
        raise _not_implemented("project registration")

    @app.delete("/api/v1/projects/{project_name}")
    async def remove_project(project_name: str) -> dict:
        raise _not_implemented("project removal")

    return app
