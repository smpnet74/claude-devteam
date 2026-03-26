"""Tests for copying agents into a project's .claude/agents/ on registration."""

from pathlib import Path

import pytest
from devteam.agents.template_manager import copy_agent_templates, copy_agents_to_project


class TestCopyAgentsToProject:
    def _setup_global_agents(self, tmp_path: Path) -> Path:
        """Set up a fake ~/.devteam/agents/ directory with templates."""
        global_agents = tmp_path / "devteam" / "agents"
        copy_agent_templates(global_agents)
        return global_agents

    def test_copies_agents_to_project(self, tmp_path):
        global_agents = self._setup_global_agents(tmp_path)
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()

        copy_agents_to_project(global_agents, project_dir)

        claude_agents = project_dir / ".claude" / "agents"
        assert claude_agents.is_dir()
        assert len(list(claude_agents.glob("*.md"))) == 16
        assert (claude_agents / "ceo.md").exists()

    def test_creates_claude_directory(self, tmp_path):
        global_agents = self._setup_global_agents(tmp_path)
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()

        copy_agents_to_project(global_agents, project_dir)
        assert (project_dir / ".claude" / "agents").is_dir()

    def test_project_dir_must_exist(self, tmp_path):
        global_agents = self._setup_global_agents(tmp_path)
        with pytest.raises(FileNotFoundError):
            copy_agents_to_project(global_agents, tmp_path / "nonexistent")

    def test_empty_global_agents_dir_raises(self, tmp_path):
        """Global agents dir exists but has no .md files."""
        empty_global = tmp_path / "empty_agents"
        empty_global.mkdir()
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="No agent template files"):
            copy_agents_to_project(empty_global, project_dir)

    def test_preserves_existing_project_agents(self, tmp_path):
        global_agents = self._setup_global_agents(tmp_path)
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        claude_agents = project_dir / ".claude" / "agents"
        claude_agents.mkdir(parents=True)
        custom = claude_agents / "ceo.md"
        custom.write_text("customized CEO")

        copy_agents_to_project(global_agents, project_dir, overwrite=False)
        assert custom.read_text() == "customized CEO"
