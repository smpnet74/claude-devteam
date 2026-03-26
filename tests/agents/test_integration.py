"""Integration test: load templates -> build registry -> build invoker -> verify params."""

from devteam.agents import (
    AgentInvoker,
    AgentRegistry,
    InvocationContext,
    copy_agent_templates,
    get_bundled_templates_dir,
)


class TestFullPipeline:
    """End-to-end: templates -> registry -> invoker parameter generation."""

    def test_bundled_templates_load_into_registry(self):
        templates_dir = get_bundled_templates_dir()
        registry = AgentRegistry.load(templates_dir)
        assert len(registry) == 16

    def test_copied_templates_load_into_registry(self, tmp_path):
        dest = tmp_path / "agents"
        copy_agent_templates(dest)
        registry = AgentRegistry.load(dest)
        assert len(registry) == 16

    def test_invoker_builds_params_for_all_roles(self, tmp_path):
        templates_dir = get_bundled_templates_dir()
        registry = AgentRegistry.load(templates_dir)
        invoker = AgentInvoker(registry)
        context = InvocationContext(worktree_path=tmp_path, project_name="test")

        for role in registry.list_roles():
            params = invoker.build_query_params(
                role=role,
                task_prompt=f"Test task for {role}",
                context=context,
            )
            assert params["model"] in ("opus", "sonnet", "haiku")
            assert params["permission_mode"] == "bypassPermissions"
            assert "json_schema" in params
            assert isinstance(params["allowed_tools"], list)
            assert len(params["allowed_tools"]) > 0

    def test_ceo_gets_routing_schema(self):
        templates_dir = get_bundled_templates_dir()
        registry = AgentRegistry.load(templates_dir)
        invoker = AgentInvoker(registry)
        schema = invoker.schema_for_role("ceo")
        assert "path" in schema["properties"]
        assert "reasoning" in schema["properties"]

    def test_engineers_get_implementation_schema(self):
        templates_dir = get_bundled_templates_dir()
        registry = AgentRegistry.load(templates_dir)
        invoker = AgentInvoker(registry)

        for role in [
            "backend_engineer",
            "frontend_engineer",
            "devops_engineer",
            "data_engineer",
            "infra_engineer",
            "tooling_engineer",
            "cloud_engineer",
        ]:
            schema = invoker.schema_for_role(role)
            assert "status" in schema["properties"]
            assert "files_changed" in schema["properties"]

    def test_reviewers_get_review_schema(self):
        templates_dir = get_bundled_templates_dir()
        registry = AgentRegistry.load(templates_dir)
        invoker = AgentInvoker(registry)

        for role in ["em_team_a", "em_team_b", "qa_engineer", "security_engineer", "tech_writer"]:
            schema = invoker.schema_for_role(role)
            assert "verdict" in schema["properties"]
            assert "comments" in schema["properties"]
