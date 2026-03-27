"""Load concurrency-related configuration from config.toml.

Reads [general].max_concurrent_agents and [rate_limit].default_backoff_seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ConcurrencyConfig:
    """Concurrency and rate limit configuration."""

    max_concurrent_agents: int
    default_backoff_seconds: int


def load_concurrency_config(config: dict[str, Any]) -> ConcurrencyConfig:
    """Load concurrency config from a parsed config.toml dict.

    Args:
        config: Parsed TOML configuration dictionary.

    Returns:
        ConcurrencyConfig with validated values.

    Raises:
        ValueError: If any value is invalid.
    """
    general = config.get("general", {})
    rate_limit = config.get("rate_limit", {})

    max_concurrent = general.get("max_concurrent_agents", 3)
    backoff = rate_limit.get("default_backoff_seconds", 1800)

    if not isinstance(max_concurrent, int) or max_concurrent <= 0:
        raise ValueError("max_concurrent_agents must be a positive integer")
    if not isinstance(backoff, int) or backoff <= 0:
        raise ValueError("default_backoff_seconds must be a positive integer")

    return ConcurrencyConfig(
        max_concurrent_agents=max_concurrent,
        default_backoff_seconds=backoff,
    )
