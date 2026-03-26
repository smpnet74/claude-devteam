"""Tests that all 16 agent template .md files parse correctly."""

from pathlib import Path

import pytest
from devteam.agents.registry import AgentRegistry


TEMPLATES_DIR = Path(__file__).parent.parent.parent / "src" / "devteam" / "templates" / "agents"

EXPECTED_AGENTS = {
    "ceo": ("opus", ["Read", "Glob", "Grep"]),
    "chief_architect": ("opus", 9),
    "planner_researcher_a": ("sonnet", 9),
    "planner_researcher_b": ("sonnet", 9),
    "em_team_a": ("sonnet", 9),
    "em_team_b": ("sonnet", 9),
    "backend_engineer": ("sonnet", 9),
    "frontend_engineer": ("sonnet", 9),
    "devops_engineer": ("sonnet", 9),
    "data_engineer": ("sonnet", 9),
    "infra_engineer": ("sonnet", 9),
    "tooling_engineer": ("sonnet", 9),
    "cloud_engineer": ("sonnet", 9),
    "qa_engineer": ("haiku", 9),
    "security_engineer": ("haiku", 9),
    "tech_writer": ("haiku", 9),
}


class TestAgentTemplates:
    def test_templates_directory_exists(self):
        assert TEMPLATES_DIR.is_dir(), f"Templates directory not found: {TEMPLATES_DIR}"

    def test_all_16_agents_present(self):
        md_files = {f.stem for f in TEMPLATES_DIR.glob("*.md")}
        expected = set(EXPECTED_AGENTS.keys())
        assert md_files == expected, f"Missing: {expected - md_files}, Extra: {md_files - expected}"

    def test_registry_loads_all_templates(self):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        assert len(registry) == 16

    @pytest.mark.parametrize("role", EXPECTED_AGENTS.keys())
    def test_agent_parses_correctly(self, role):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        defn = registry.get(role)
        expected_model, expected_tools = EXPECTED_AGENTS[role]
        assert defn.model == expected_model, (
            f"{role}: expected model {expected_model}, got {defn.model}"
        )
        if isinstance(expected_tools, list):
            assert defn.tools == expected_tools, f"{role}: tools mismatch"
        else:
            assert len(defn.tools) == expected_tools, (
                f"{role}: expected {expected_tools} tools, got {len(defn.tools)}"
            )

    @pytest.mark.parametrize("role", EXPECTED_AGENTS.keys())
    def test_agent_has_nonempty_prompt(self, role):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        defn = registry.get(role)
        assert len(defn.prompt) > 50, f"{role}: prompt too short ({len(defn.prompt)} chars)"

    def test_ceo_has_restricted_tools(self):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        ceo = registry.get("ceo")
        assert "Bash" not in ceo.tools
        assert "Edit" not in ceo.tools
        assert "Write" not in ceo.tools
        assert "Read" in ceo.tools

    def test_engineers_have_full_tools(self):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        for role in [
            "backend_engineer",
            "frontend_engineer",
            "devops_engineer",
            "data_engineer",
            "infra_engineer",
            "tooling_engineer",
            "cloud_engineer",
        ]:
            defn = registry.get(role)
            assert "Bash" in defn.tools, f"{role} missing Bash"
            assert "Edit" in defn.tools, f"{role} missing Edit"
            assert "query_knowledge" in defn.tools, f"{role} missing query_knowledge"

    def test_validation_agents_use_haiku(self):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        for role in ["qa_engineer", "security_engineer", "tech_writer"]:
            defn = registry.get(role)
            assert defn.model == "haiku", f"{role} should use haiku, got {defn.model}"

    def test_executive_agents_use_opus(self):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        for role in ["ceo", "chief_architect"]:
            defn = registry.get(role)
            assert defn.model == "opus", f"{role} should use opus, got {defn.model}"
