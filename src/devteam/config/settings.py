"""Configuration loading from TOML files.

Global config: ~/.devteam/config.toml
Project config: <project-root>/devteam.toml

Project settings override global settings where applicable.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# --- Config Section Models ---


class DaemonConfig(BaseModel):
    """Daemon process configuration."""

    port: int = 7432


class GeneralConfig(BaseModel):
    """General operational settings."""

    max_concurrent_agents: int = 3


class ModelsConfig(BaseModel):
    """Model tier assignments."""

    executive: str = "opus"
    engineering: str = "sonnet"
    validation: str = "haiku"
    extraction: str = "haiku"


class ApprovalConfig(BaseModel):
    """Approval policy configuration.

    push_to_main is a hard block -- always "never", cannot be overridden.
    """

    commit: str = "auto"
    push: str = "auto"
    open_pr: str = "auto"
    merge: str = "auto"
    cleanup: str = "auto"
    push_to_main: str = "never"

    @model_validator(mode="after")
    def enforce_push_to_main_never(self) -> "ApprovalConfig":
        """push_to_main = 'never' is a hard safety block."""
        self.push_to_main = "never"
        return self


class KnowledgeConfig(BaseModel):
    """Knowledge system configuration."""

    embedding_model: str = "nomic-embed-text"
    surrealdb_url: str = "ws://localhost:8000"
    cross_project_sharing: str = "layered"


class RateLimitConfig(BaseModel):
    """Rate limit handling configuration."""

    default_backoff_seconds: int = 1800


class PRConfig(BaseModel):
    """PR lifecycle configuration."""

    max_fix_iterations: int = 5
    ci_poll_interval_seconds: int = 60


class GitConfig(BaseModel):
    """Git operations configuration."""

    worktree_dir: str = ".worktrees"


class ProjectInfo(BaseModel):
    """Per-project metadata."""

    name: str = ""
    repos: list[str] = Field(default_factory=list)


class ExecutionConfig(BaseModel):
    """Per-project execution commands."""

    test_command: Optional[str] = None
    lint_command: Optional[str] = None
    build_command: Optional[str] = None
    merge_strategy: str = "squash"
    pr_template: Optional[str] = None


# --- Top-Level Config Models ---


class DevteamConfig(BaseModel):
    """Complete devteam configuration (global)."""

    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    pr: PRConfig = Field(default_factory=PRConfig)
    git: GitConfig = Field(default_factory=GitConfig)


class ProjectConfig(BaseModel):
    """Per-project devteam.toml configuration."""

    project: ProjectInfo = Field(default_factory=ProjectInfo)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)


# --- Loading Functions ---


def load_global_config(config_path: Path) -> DevteamConfig:
    """Load global configuration from ~/.devteam/config.toml.

    Returns defaults if the file does not exist or is empty.
    """
    if not config_path.exists():
        return DevteamConfig()

    text = config_path.read_text()
    if not text.strip():
        return DevteamConfig()

    data = tomllib.loads(text)
    return DevteamConfig(**data)


def load_project_config(config_path: Path) -> Optional[ProjectConfig]:
    """Load per-project configuration from devteam.toml.

    Returns None if the file does not exist.
    """
    if not config_path.exists():
        return None

    text = config_path.read_text()
    if not text.strip():
        return ProjectConfig()

    data = tomllib.loads(text)
    return ProjectConfig(**data)


def merge_configs(
    global_config: DevteamConfig,
    project_config: Optional[ProjectConfig],
) -> DevteamConfig:
    """Merge project config into global config.

    Project-level approval settings override global approval settings.
    All other global settings are preserved.
    """
    if project_config is None:
        return global_config

    merged_data = global_config.model_dump()

    # Merge approval overrides from project
    project_approval = project_config.approval.model_dump(exclude_defaults=True)
    for key, value in project_approval.items():
        merged_data["approval"][key] = value

    return DevteamConfig(**merged_data)
