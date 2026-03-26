"""Tests for agent registry — parses .md frontmatter, builds tool/model registry."""

from pathlib import Path

import pytest
from devteam.agents.registry import AgentDefinition, AgentRegistry


SAMPLE_AGENT_MD = """\
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Backend Engineer for the development team.

## Expertise
APIs, databases, service architecture, migrations, ORM patterns.

## Working Style
- Read existing code before proposing changes
- Follow project conventions discovered in the codebase
- Write tests alongside implementation
- Create focused, atomic commits

## Completion Protocol
When your work is complete:
1. Ensure all tests pass
2. Summarize what you built and any decisions you made
3. Flag anything you're uncertain about as a question
"""

CEO_AGENT_MD = """\
---
model: opus
tools:
  - Read
  - Glob
  - Grep
---

You are the CEO of the development team.

## Expertise
Intake, routing, orchestration. Never touches code.
"""

INVALID_FRONTMATTER_MD = """\
No frontmatter here, just text.

This should fail to parse.
"""

MISSING_MODEL_MD = """\
---
tools:
  - Read
---

Missing the model field.
"""


class TestAgentDefinition:
    def test_parse_valid_agent(self):
        defn = AgentDefinition.from_markdown(SAMPLE_AGENT_MD, "backend_engineer")
        assert defn.role == "backend_engineer"
        assert defn.model == "sonnet"
        assert "Read" in defn.tools
        assert "Bash" in defn.tools
        assert "query_knowledge" in defn.tools
        assert len(defn.tools) == 9
        assert "You are the Backend Engineer" in defn.prompt

    def test_parse_ceo_agent(self):
        defn = AgentDefinition.from_markdown(CEO_AGENT_MD, "ceo")
        assert defn.role == "ceo"
        assert defn.model == "opus"
        assert defn.tools == ["Read", "Glob", "Grep"]
        assert "Bash" not in defn.tools
        assert "You are the CEO" in defn.prompt

    def test_prompt_excludes_frontmatter(self):
        defn = AgentDefinition.from_markdown(SAMPLE_AGENT_MD, "backend_engineer")
        assert "---" not in defn.prompt
        assert "model:" not in defn.prompt
        assert "tools:" not in defn.prompt

    def test_invalid_frontmatter_raises(self):
        with pytest.raises(ValueError, match="frontmatter"):
            AgentDefinition.from_markdown(INVALID_FRONTMATTER_MD, "bad")

    def test_missing_model_raises(self):
        with pytest.raises(ValueError, match="model"):
            AgentDefinition.from_markdown(MISSING_MODEL_MD, "bad")


class TestAgentRegistry:
    def _create_agents_dir(self, tmp_path: Path) -> Path:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "backend_engineer.md").write_text(SAMPLE_AGENT_MD)
        (agents_dir / "ceo.md").write_text(CEO_AGENT_MD)
        return agents_dir

    def test_load_from_directory(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        registry = AgentRegistry.load(agents_dir)
        assert len(registry) == 2
        assert "backend_engineer" in registry
        assert "ceo" in registry

    def test_get_agent(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        registry = AgentRegistry.load(agents_dir)
        defn = registry.get("backend_engineer")
        assert defn.model == "sonnet"
        assert "Bash" in defn.tools

    def test_get_unknown_agent_raises(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        registry = AgentRegistry.load(agents_dir)
        with pytest.raises(KeyError):
            registry.get("nonexistent_agent")

    def test_get_tools(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        registry = AgentRegistry.load(agents_dir)
        tools = registry.get_tools("ceo")
        assert tools == ["Read", "Glob", "Grep"]

    def test_get_model(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        registry = AgentRegistry.load(agents_dir)
        assert registry.get_model("ceo") == "opus"
        assert registry.get_model("backend_engineer") == "sonnet"

    def test_list_roles(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        registry = AgentRegistry.load(agents_dir)
        roles = registry.list_roles()
        assert set(roles) == {"backend_engineer", "ceo"}

    def test_ignores_non_md_files(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        (agents_dir / "README.txt").write_text("Not an agent")
        (agents_dir / ".DS_Store").write_text("")
        registry = AgentRegistry.load(agents_dir)
        assert len(registry) == 2

    def test_empty_directory(self, tmp_path):
        agents_dir = tmp_path / "empty_agents"
        agents_dir.mkdir()
        registry = AgentRegistry.load(agents_dir)
        assert len(registry) == 0

    def test_directory_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            AgentRegistry.load(tmp_path / "nonexistent")
