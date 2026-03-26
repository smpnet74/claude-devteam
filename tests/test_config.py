"""Tests for configuration loading."""

from pathlib import Path

import pytest

from devteam.config.settings import (
    ConfigError,
    DevteamConfig,
    load_global_config,
    load_project_config,
    merge_configs,
)


class TestDefaultConfig:
    def test_default_daemon_config(self) -> None:
        config = DevteamConfig()
        assert config.daemon.port == 7432

    def test_default_general_config(self) -> None:
        config = DevteamConfig()
        assert config.general.max_concurrent_agents == 3

    def test_default_approval_config(self) -> None:
        config = DevteamConfig()
        assert config.approval.commit == "auto"
        assert config.approval.push_to_main == "never"

    def test_default_rate_limit_config(self) -> None:
        config = DevteamConfig()
        assert config.rate_limit.default_backoff_seconds == 1800

    def test_default_pr_config(self) -> None:
        config = DevteamConfig()
        assert config.pr.max_fix_iterations == 5
        assert config.pr.ci_poll_interval_seconds == 60

    def test_default_git_config(self) -> None:
        config = DevteamConfig()
        assert config.git.worktree_dir == ".worktrees"


class TestLoadGlobalConfig:
    def test_load_from_toml_file(self, tmp_devteam_home: Path) -> None:
        config_path = tmp_devteam_home / "config.toml"
        config_path.write_text(
            """\
[daemon]
port = 8080

[general]
max_concurrent_agents = 5

[approval]
merge = "manual"
"""
        )
        config = load_global_config(config_path)
        assert config.daemon.port == 8080
        assert config.general.max_concurrent_agents == 5
        assert config.approval.merge == "manual"
        # Defaults preserved for unspecified values
        assert config.approval.commit == "auto"
        assert config.approval.push_to_main == "never"

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        config = load_global_config(tmp_path / "nonexistent.toml")
        assert config.daemon.port == 7432
        assert config.general.max_concurrent_agents == 3

    def test_load_empty_file_returns_defaults(self, tmp_devteam_home: Path) -> None:
        config_path = tmp_devteam_home / "config.toml"
        config_path.write_text("")
        config = load_global_config(config_path)
        assert config.daemon.port == 7432


class TestLoadProjectConfig:
    def test_load_project_config(self, tmp_project_dir: Path) -> None:
        config_path = tmp_project_dir / "devteam.toml"
        config_path.write_text(
            """\
[project]
name = "myapp"
repos = ["github.com/user/myapp-api", "github.com/user/myapp-ui"]

[approval]
merge = "manual"

[execution]
test_command = "npm test"
lint_command = "npm run lint"
build_command = "npm run build"
merge_strategy = "squash"
"""
        )
        config = load_project_config(config_path)
        assert config is not None
        assert config.project.name == "myapp"
        assert len(config.project.repos) == 2
        assert config.approval.merge == "manual"
        assert config.execution.test_command == "npm test"

    def test_load_missing_project_config_returns_none(self, tmp_path: Path) -> None:
        config = load_project_config(tmp_path / "nonexistent.toml")
        assert config is None


class TestMergeConfigs:
    def test_project_overrides_global(self, tmp_devteam_home: Path, tmp_project_dir: Path) -> None:
        global_path = tmp_devteam_home / "config.toml"
        global_path.write_text(
            """\
[approval]
merge = "auto"
commit = "auto"
"""
        )
        project_path = tmp_project_dir / "devteam.toml"
        project_path.write_text(
            """\
[project]
name = "myapp"

[approval]
merge = "manual"
"""
        )
        global_config = load_global_config(global_path)
        project_config = load_project_config(project_path)
        merged = merge_configs(global_config, project_config)

        assert merged.approval.merge == "manual"  # overridden
        assert merged.approval.commit == "auto"  # preserved from global

    def test_merge_with_no_project_config(self, tmp_devteam_home: Path) -> None:
        global_path = tmp_devteam_home / "config.toml"
        global_path.write_text("[daemon]\nport = 9999\n")
        global_config = load_global_config(global_path)
        merged = merge_configs(global_config, None)
        assert merged.daemon.port == 9999


class TestPushToMainNeverOverridable:
    def test_push_to_main_stays_never(self, tmp_devteam_home: Path) -> None:
        """push_to_main = 'never' is a hard block that cannot be overridden."""
        config_path = tmp_devteam_home / "config.toml"
        config_path.write_text(
            """\
[approval]
push_to_main = "auto"
"""
        )
        config = load_global_config(config_path)
        # push_to_main is always forced to "never" regardless of config
        assert config.approval.push_to_main == "never"

    def test_push_to_main_stays_never_in_project_config(
        self, tmp_devteam_home: Path, tmp_project_dir: Path
    ) -> None:
        """push_to_main cannot be overridden via project config either."""
        global_path = tmp_devteam_home / "config.toml"
        global_path.write_text('[approval]\npush_to_main = "never"\n')

        project_path = tmp_project_dir / "devteam.toml"
        project_path.write_text(
            """\
[project]
name = "malicious"

[approval]
push_to_main = "auto"
"""
        )
        global_config = load_global_config(global_path)
        project_config = load_project_config(project_path)
        merged = merge_configs(global_config, project_config)

        assert merged.approval.push_to_main == "never"


class TestMalformedConfig:
    def test_malformed_global_config_raises_config_error(self, tmp_devteam_home: Path) -> None:
        config_path = tmp_devteam_home / "config.toml"
        config_path.write_text("this is not [valid toml ===")
        with pytest.raises(ConfigError):
            load_global_config(config_path)

    def test_malformed_project_config_raises_config_error(self, tmp_project_dir: Path) -> None:
        config_path = tmp_project_dir / "devteam.toml"
        config_path.write_text("this is not [valid toml ===")
        with pytest.raises(ConfigError):
            load_project_config(config_path)
