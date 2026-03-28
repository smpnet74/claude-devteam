"""Durable runtime state store — SQLite-backed registry for aliases, artifacts, and questions.

Persists at ~/.devteam/runtime.sqlite. Survives process restart.
Used by bootstrap, resume, status, answer, and cleanup operations.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JobRecord:
    alias: str
    workflow_id: str
    project_name: str
    repo_root: str
    status: str
    created_at: float


@dataclass(frozen=True)
class TaskRecord:
    alias: str
    workflow_id: str
    job_alias: str
    assigned_to: str
    status: str


@dataclass(frozen=True)
class QuestionRecord:
    display_alias: str
    internal_id: str
    child_workflow_id: str
    task_alias: str
    text: str
    tier: int
    resolved: bool


@dataclass(frozen=True)
class ArtifactRecord:
    task_alias: str
    worktree_path: str
    branch_name: str
    pr_number: int | None
    pr_url: str | None
    pr_state: str | None


class RuntimeStateStore:
    """SQLite-backed registry for runtime metadata."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS job_registry (
                alias TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL UNIQUE,
                project_name TEXT NOT NULL DEFAULT '',
                repo_root TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_registry (
                alias TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL UNIQUE,
                job_alias TEXT NOT NULL REFERENCES job_registry(alias),
                assigned_to TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE IF NOT EXISTS question_registry (
                display_alias TEXT PRIMARY KEY,
                internal_id TEXT NOT NULL,
                child_workflow_id TEXT NOT NULL,
                task_alias TEXT NOT NULL,
                text TEXT NOT NULL,
                tier INTEGER NOT NULL DEFAULT 2,
                resolved INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS artifact_registry (
                task_alias TEXT PRIMARY KEY REFERENCES task_registry(alias),
                worktree_path TEXT NOT NULL DEFAULT '',
                branch_name TEXT NOT NULL DEFAULT '',
                pr_number INTEGER,
                pr_url TEXT,
                pr_state TEXT
            );
        """)
        self._conn.commit()

    def _next_job_alias(self) -> str:
        row = self._conn.execute(
            "SELECT alias FROM job_registry ORDER BY CAST(SUBSTR(alias, 3) AS INTEGER) DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return "W-1"
        num = int(row[0].split("-")[1])
        return f"W-{num + 1}"

    def register_job(self, workflow_id: str, project_name: str, repo_root: str) -> JobRecord:
        alias = self._next_job_alias()
        now = time.time()
        self._conn.execute(
            "INSERT INTO job_registry (alias, workflow_id, project_name, repo_root, status, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (alias, workflow_id, project_name, repo_root, now),
        )
        self._conn.commit()
        return JobRecord(
            alias=alias,
            workflow_id=workflow_id,
            project_name=project_name,
            repo_root=repo_root,
            status="active",
            created_at=now,
        )

    def get_job(self, alias: str) -> JobRecord | None:
        row = self._conn.execute(
            "SELECT alias, workflow_id, project_name, repo_root, status, created_at "
            "FROM job_registry WHERE alias = ?",
            (alias,),
        ).fetchone()
        return JobRecord(*row) if row else None

    def get_job_by_workflow_id(self, workflow_id: str) -> JobRecord | None:
        row = self._conn.execute(
            "SELECT alias, workflow_id, project_name, repo_root, status, created_at "
            "FROM job_registry WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        return JobRecord(*row) if row else None

    def update_job_status(self, alias: str, status: str) -> None:
        self._conn.execute("UPDATE job_registry SET status = ? WHERE alias = ?", (status, alias))
        self._conn.commit()

    def get_active_jobs(self) -> list[JobRecord]:
        rows = self._conn.execute(
            "SELECT alias, workflow_id, project_name, repo_root, status, created_at "
            "FROM job_registry WHERE status IN ('active', 'paused')"
        ).fetchall()
        return [JobRecord(*r) for r in rows]

    def register_task(
        self, alias: str, workflow_id: str, job_alias: str, assigned_to: str
    ) -> TaskRecord:
        self._conn.execute(
            "INSERT INTO task_registry (alias, workflow_id, job_alias, assigned_to, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (alias, workflow_id, job_alias, assigned_to),
        )
        self._conn.commit()
        return TaskRecord(
            alias=alias,
            workflow_id=workflow_id,
            job_alias=job_alias,
            assigned_to=assigned_to,
            status="pending",
        )

    def get_task(self, alias: str) -> TaskRecord | None:
        row = self._conn.execute(
            "SELECT alias, workflow_id, job_alias, assigned_to, status "
            "FROM task_registry WHERE alias = ?",
            (alias,),
        ).fetchone()
        return TaskRecord(*row) if row else None

    def get_tasks_for_job(self, job_alias: str) -> list[TaskRecord]:
        rows = self._conn.execute(
            "SELECT alias, workflow_id, job_alias, assigned_to, status "
            "FROM task_registry WHERE job_alias = ?",
            (job_alias,),
        ).fetchall()
        return [TaskRecord(*r) for r in rows]

    def update_task_status(self, alias: str, status: str) -> None:
        self._conn.execute("UPDATE task_registry SET status = ? WHERE alias = ?", (status, alias))
        self._conn.commit()

    def _next_question_alias(self) -> str:
        row = self._conn.execute(
            "SELECT display_alias FROM question_registry "
            "ORDER BY CAST(SUBSTR(display_alias, 3) AS INTEGER) DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return "Q-1"
        num = int(row[0].split("-")[1])
        return f"Q-{num + 1}"

    def register_question(
        self, internal_id: str, child_workflow_id: str, task_alias: str, text: str, tier: int
    ) -> str:
        display = self._next_question_alias()
        self._conn.execute(
            "INSERT INTO question_registry "
            "(display_alias, internal_id, child_workflow_id, task_alias, text, tier, resolved) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (display, internal_id, child_workflow_id, task_alias, text, tier),
        )
        self._conn.commit()
        return display

    def lookup_question(self, display_alias: str) -> QuestionRecord | None:
        row = self._conn.execute(
            "SELECT display_alias, internal_id, child_workflow_id, task_alias, text, tier, resolved "
            "FROM question_registry WHERE display_alias = ?",
            (display_alias,),
        ).fetchone()
        return QuestionRecord(*row[:6], resolved=bool(row[6])) if row else None

    def resolve_question(self, display_alias: str) -> QuestionRecord | None:
        self._conn.execute(
            "UPDATE question_registry SET resolved = 1 WHERE display_alias = ?", (display_alias,)
        )
        self._conn.commit()
        return self.lookup_question(display_alias)

    def get_pending_questions(self, job_alias: str | None = None) -> list[QuestionRecord]:
        if job_alias:
            rows = self._conn.execute(
                "SELECT q.display_alias, q.internal_id, q.child_workflow_id, q.task_alias, "
                "q.text, q.tier, q.resolved "
                "FROM question_registry q JOIN task_registry t ON q.task_alias = t.alias "
                "WHERE q.resolved = 0 AND t.job_alias = ?",
                (job_alias,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT display_alias, internal_id, child_workflow_id, task_alias, text, tier, resolved "
                "FROM question_registry WHERE resolved = 0"
            ).fetchall()
        return [QuestionRecord(*r[:6], resolved=bool(r[6])) for r in rows]

    def register_artifact(self, task_alias: str, worktree_path: str, branch_name: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO artifact_registry (task_alias, worktree_path, branch_name) "
            "VALUES (?, ?, ?)",
            (task_alias, worktree_path, branch_name),
        )
        self._conn.commit()

    def update_pr(self, task_alias: str, pr_number: int, pr_url: str, pr_state: str) -> None:
        self._conn.execute(
            "UPDATE artifact_registry SET pr_number = ?, pr_url = ?, pr_state = ? "
            "WHERE task_alias = ?",
            (pr_number, pr_url, pr_state, task_alias),
        )
        self._conn.commit()

    def get_artifact(self, task_alias: str) -> ArtifactRecord | None:
        row = self._conn.execute(
            "SELECT task_alias, worktree_path, branch_name, pr_number, pr_url, pr_state "
            "FROM artifact_registry WHERE task_alias = ?",
            (task_alias,),
        ).fetchone()
        return ArtifactRecord(*row) if row else None

    def get_artifacts_for_job(self, job_alias: str) -> list[ArtifactRecord]:
        rows = self._conn.execute(
            "SELECT a.task_alias, a.worktree_path, a.branch_name, a.pr_number, a.pr_url, a.pr_state "
            "FROM artifact_registry a JOIN task_registry t ON a.task_alias = t.alias "
            "WHERE t.job_alias = ?",
            (job_alias,),
        ).fetchall()
        return [ArtifactRecord(*r) for r in rows]

    def close(self) -> None:
        self._conn.close()
