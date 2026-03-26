"""Configuration loading from TOML files.

Global config: ~/.devteam/config.toml
Project config: <project-root>/devteam.toml

Project settings override global settings where applicable.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator


# --- Config Section Models ---


class DaemonConfig(BaseModel):
    """Daemon process configuration."""

    port: int = Field(default=7432, gt=0, le=65535)


class GeneralConfig(BaseModel):
    """General operational settings."""

    max_concurrent_agents: int = Field(default=3, gt=0)


class ModelsConfig(BaseModel):
    """Model tier assignments."""

    executive: str = "opus"
    engineering: str = "sonnet"
    validation: str = "haiku"
    extraction: str = "haiku"


ApprovalLevel = Literal["auto", "manual", "never"]


class ApprovalConfig(BaseModel):
    """Approval policy configuration.

    push_to_main is a hard block -- always "never", cannot be overridden.
    """

    commit: ApprovalLevel = "auto"
    push: ApprovalLevel = "auto"
    open_pr: ApprovalLevel = "auto"
    merge: ApprovalLevel = "auto"
    cleanup: ApprovalLevel = "auto"
    push_to_main: ApprovalLevel = "never"

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

    default_backoff_seconds: int = Field(default=1800, gt=0)


class PRConfig(BaseModel):
    """PR lifecycle configuration."""

    max_fix_iterations: int = Field(default=5, gt=0)
    ci_poll_interval_seconds: int = Field(default=60, gt=0)


class GitConfig(BaseModel):
    """Git operations configuration."""

    worktree_dir: str = ".worktrees"


class ProjectInfo(BaseModel):
    """Per-project metadata."""

    name: str = ""
    repos: list[str] = Field(default_factory=list)


class ExecutionConfig(BaseModel):
    """Per-project execution commands."""

    test_command: str | None = None
    lint_command: str | None = None
    build_command: str | None = None
    merge_strategy: str = "squash"
    pr_template: str | None = None


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
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)


class ProjectConfig(BaseModel):
    """Per-project devteam.toml configuration."""

    project: ProjectInfo = Field(default_factory=ProjectInfo)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)


class ConfigError(Exception):
    """Raised when a configuration file is malformed or contains invalid values."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        super().__init__(f"Error loading {path}: {reason}")


# --- Loading Functions ---


def load_global_config(config_path: Path) -> DevteamConfig:
    """Load global configuration from ~/.devteam/config.toml.

    Returns defaults if the file does not exist or is empty.
    Raises ConfigError on malformed TOML or invalid values.
    """
    if not config_path.exists():
        return DevteamConfig()

    try:
        text = config_path.read_text()
    except OSError as e:
        raise ConfigError(config_path, f"could not read file: {e}") from e
    if not text.strip():
        return DevteamConfig()

    try:
        data = tomllib.loads(text)
        return DevteamConfig(**data)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(config_path, f"invalid TOML: {e}") from e
    except ValidationError as e:
        raise ConfigError(config_path, f"invalid config values: {e}") from e


def load_project_config(config_path: Path) -> ProjectConfig | None:
    """Load per-project configuration from devteam.toml.

    Returns None if the file does not exist.
    Raises ConfigError on malformed TOML or invalid values.
    """
    if not config_path.exists():
        return None

    try:
        text = config_path.read_text()
    except OSError as e:
        raise ConfigError(config_path, f"could not read file: {e}") from e
    if not text.strip():
        return None

    try:
        data = tomllib.loads(text)
        return ProjectConfig(**data)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(config_path, f"invalid TOML: {e}") from e
    except ValidationError as e:
        raise ConfigError(config_path, f"invalid config values: {e}") from e


def merge_configs(
    global_config: DevteamConfig,
    project_config: ProjectConfig | None,
) -> DevteamConfig:
    """Merge project config into global config.

    Project-level approval settings override global approval settings.
    All other global settings are preserved.
    """
    if project_config is None:
        return global_config

    merged_data = global_config.model_dump()

    # Merge approval overrides from project
    project_approval = project_config.approval.model_dump(exclude_unset=True)
    for key, value in project_approval.items():
        merged_data["approval"][key] = value

    # Merge execution overrides from project
    project_execution = project_config.execution.model_dump(exclude_unset=True)
    for key, value in project_execution.items():
        merged_data["execution"][key] = value

    return DevteamConfig(**merged_data)
