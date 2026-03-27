"""Tests for loading concurrency configuration from config.toml."""

import pytest
from devteam.concurrency.config import load_concurrency_config


class TestConcurrencyConfig:
    def test_load_from_full_config(self):
        config = {
            "general": {"max_concurrent_agents": 5},
            "rate_limit": {"default_backoff_seconds": 900},
        }
        cc = load_concurrency_config(config)
        assert cc.max_concurrent_agents == 5
        assert cc.default_backoff_seconds == 900

    def test_defaults_when_missing(self):
        cc = load_concurrency_config({})
        assert cc.max_concurrent_agents == 3
        assert cc.default_backoff_seconds == 1800

    def test_partial_config(self):
        config = {"general": {"max_concurrent_agents": 10}}
        cc = load_concurrency_config(config)
        assert cc.max_concurrent_agents == 10
        assert cc.default_backoff_seconds == 1800  # default

    def test_invalid_concurrency_raises(self):
        config = {"general": {"max_concurrent_agents": -1}}
        with pytest.raises(ValueError, match="must be a positive integer"):
            load_concurrency_config(config)

    def test_invalid_backoff_raises(self):
        config = {"rate_limit": {"default_backoff_seconds": 0}}
        with pytest.raises(ValueError, match="must be a positive integer"):
            load_concurrency_config(config)
