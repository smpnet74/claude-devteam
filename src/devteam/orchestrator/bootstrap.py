"""Bootstrap: config → DBOS → services → runtime_state → workflow start.

Initializes all services and wires module-level singletons in runtime.py.
Gracefully degrades when knowledge store or embedder are unavailable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from devteam.agents.invoker import AgentInvoker
from devteam.agents.registry import AgentRegistry
from devteam.agents.template_manager import get_bundled_templates_dir
from devteam.config.settings import (
    DevteamConfig,
    KnowledgeConfig,
    load_global_config,
    load_project_config,
    merge_configs,
)
from devteam.knowledge.embeddings import create_embedder_from_config
from devteam.knowledge.store import KnowledgeStore
from devteam.orchestrator.runtime import set_config, set_invoker, set_knowledge_store
from devteam.orchestrator.runtime_state import RuntimeStateStore

if TYPE_CHECKING:
    from devteam.knowledge.embeddings import OllamaEmbedder

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_runtime_store: RuntimeStateStore | None = None


def get_runtime_store() -> RuntimeStateStore:
    """Get the global RuntimeStateStore. Raises if not initialized."""
    if _runtime_store is None:
        raise RuntimeError("Runtime store not initialized")
    return _runtime_store


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_and_merge_config(
    global_path: Path | None = None,
    project_path: Path | None = None,
) -> DevteamConfig:
    """Load and merge global + project configuration."""
    if global_path is None:
        global_path = Path.home() / ".devteam" / "config.toml"
    if project_path is None:
        project_path = Path("devteam.toml")

    global_config = load_global_config(global_path)
    project_config = load_project_config(project_path)
    return merge_configs(global_config, project_config)


# ---------------------------------------------------------------------------
# Single-job enforcement
# ---------------------------------------------------------------------------


def check_single_job(store: RuntimeStateStore) -> None:
    """Raise if there's already an active job (V1 single-job constraint)."""
    active = store.get_active_jobs()
    if active:
        raise RuntimeError(
            f"Job {active[0].alias} is active. Use 'devteam resume {active[0].alias}' "
            f"or 'devteam cancel {active[0].alias}' first."
        )


# ---------------------------------------------------------------------------
# Graceful degradation helpers
# ---------------------------------------------------------------------------


async def try_connect_knowledge(
    url: str,
    username: str,
    password: str,
) -> KnowledgeStore | None:
    """Try to connect to SurrealDB knowledge store. Returns None on failure."""
    try:
        store = KnowledgeStore(url)
        await store.connect(username=username, password=password)
        return store
    except Exception:
        logger.warning("Knowledge store unavailable — proceeding without knowledge")
        return None


def try_create_embedder(config: KnowledgeConfig) -> OllamaEmbedder | None:
    """Try to create an Ollama embedder. Returns None on failure."""
    try:
        return create_embedder_from_config(config)
    except Exception:
        logger.warning("Ollama unavailable — proceeding without embeddings")
        return None


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


async def bootstrap(
    spec: str,
    plan: str,
    dbos_db_path: str | None = None,
    runtime_db_path: str | None = None,
    config: DevteamConfig | None = None,
) -> tuple[Any, str]:
    """Initialize all services and start the job workflow.

    Returns (WorkflowHandleAsync, job_alias).
    """
    global _runtime_store

    from dbos import DBOS

    # Config
    if config is None:
        config = load_and_merge_config()

    # DBOS init
    devteam_dir = Path.home() / ".devteam"
    devteam_dir.mkdir(parents=True, exist_ok=True)

    if dbos_db_path is None:
        dbos_db_path = f"sqlite:///{devteam_dir / 'devteam_system.sqlite'}"
    elif not dbos_db_path.startswith("sqlite:///"):
        dbos_db_path = f"sqlite:///{dbos_db_path}"

    DBOS(config={"name": "devteam", "system_database_url": dbos_db_path})
    DBOS.launch()

    # Runtime state (our own SQLite, not DBOS's)
    if runtime_db_path is None:
        runtime_db_path = str(devteam_dir / "runtime.sqlite")
    _runtime_store = RuntimeStateStore(runtime_db_path)

    # V1: single active job
    check_single_job(_runtime_store)

    # Knowledge (graceful degradation)
    knowledge_store = await try_connect_knowledge(
        url=config.knowledge.surrealdb_url,
        username=config.knowledge.surrealdb_username,
        password=config.knowledge.surrealdb_password,
    )

    # Embedder (graceful degradation)
    _ = try_create_embedder(config.knowledge)  # warm check; embedder used later via knowledge store

    # Agent registry + invoker
    registry = AgentRegistry.load(get_bundled_templates_dir())
    invoker = AgentInvoker(registry)

    # Wire singletons
    set_invoker(invoker)
    set_knowledge_store(knowledge_store)
    set_config(config.model_dump())

    # Start workflow
    # Note: config is available to the workflow via set_config() singleton, not as a param.
    # DBOS workflow args must be JSON-serializable; DevteamConfig is not.
    from devteam.orchestrator.workflows import execute_job

    repo_root = str(Path.cwd())
    project_name = config.general.project_name or Path.cwd().name

    handle = await DBOS.start_workflow_async(
        execute_job,
        spec=spec,
        plan=plan,
        project_name=project_name,
        repo_root=repo_root,
    )

    # Register in runtime state (durable alias)
    job_record = _runtime_store.register_job(
        workflow_id=handle.workflow_id,
        project_name=project_name,
        repo_root=repo_root,
    )

    return handle, job_record.alias
