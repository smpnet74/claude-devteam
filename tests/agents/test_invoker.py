"""Tests for agent invoker — wraps Claude Agent SDK query() calls."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from devteam.agents.contracts import (
    ImplementationResult,
    ReviewResult,
    RoutingResult,
)
from devteam.agents.invoker import AgentInvoker, InvocationContext, InvocationError
from devteam.agents.registry import AgentDefinition, AgentRegistry

_MOCK_TARGET = "devteam.agents.invoker._run_query"


@pytest.fixture
def mock_registry():
    """Create a registry with a few test agents."""
    agents = {
        "backend_engineer": AgentDefinition(
            role="backend_engineer",
            model="sonnet",
            tools=[
                "Read",
                "Edit",
                "Write",
                "Bash",
                "Glob",
                "Grep",
                "WebSearch",
                "WebFetch",
                "query_knowledge",
            ],
            prompt="You are the Backend Engineer.",
        ),
        "ceo": AgentDefinition(
            role="ceo",
            model="opus",
            tools=["Read", "Glob", "Grep"],
            prompt="You are the CEO.",
        ),
        "qa_engineer": AgentDefinition(
            role="qa_engineer",
            model="haiku",
            tools=[
                "Read",
                "Edit",
                "Write",
                "Bash",
                "Glob",
                "Grep",
                "WebSearch",
                "WebFetch",
                "query_knowledge",
            ],
            prompt="You are the QA Engineer.",
        ),
    }
    return AgentRegistry(agents)


@pytest.fixture
def invoker(mock_registry):
    return AgentInvoker(mock_registry)


@pytest.fixture
def context(tmp_path):
    return InvocationContext(
        worktree_path=tmp_path,
        project_name="test-project",
    )


class TestInvocationContext:
    def test_creation(self, tmp_path):
        ctx = InvocationContext(
            worktree_path=tmp_path,
            project_name="my-app",
        )
        assert ctx.worktree_path == tmp_path
        assert ctx.project_name == "my-app"


class TestAgentInvoker:
    def test_schema_for_role_engineer(self, invoker):
        schema = invoker.schema_for_role("backend_engineer")
        assert schema == ImplementationResult.model_json_schema()

    def test_schema_for_role_ceo(self, invoker):
        schema = invoker.schema_for_role("ceo")
        assert schema == RoutingResult.model_json_schema()

    def test_schema_for_role_qa(self, invoker):
        schema = invoker.schema_for_role("qa_engineer")
        assert schema == ReviewResult.model_json_schema()

    def test_build_query_params_engineer(self, invoker, context):
        params = invoker.build_query_params(
            role="backend_engineer",
            task_prompt="Implement the login endpoint",
            context=context,
        )
        assert params["prompt"] == "Implement the login endpoint"
        assert params["model"] == "sonnet"
        assert params["cwd"] == str(context.worktree_path)
        assert params["allowed_tools"] == [
            "Read",
            "Edit",
            "Write",
            "Bash",
            "Glob",
            "Grep",
            "WebSearch",
            "WebFetch",
            "query_knowledge",
        ]
        assert params["permission_mode"] == "bypassPermissions"
        assert "json_schema" in params

    def test_build_query_params_ceo(self, invoker, context):
        params = invoker.build_query_params(
            role="ceo",
            task_prompt="Route this incoming request",
            context=context,
        )
        assert params["model"] == "opus"
        assert params["allowed_tools"] == ["Read", "Glob", "Grep"]

    def test_build_query_params_unknown_role_raises(self, invoker, context):
        with pytest.raises(KeyError):
            invoker.build_query_params(
                role="unknown_agent",
                task_prompt="test",
                context=context,
            )

    @pytest.mark.asyncio
    async def test_invoke_calls_run_query(self, invoker, context):
        """Test that invoke() calls _run_query with correct params."""
        mock_result = MagicMock()
        mock_result.result = json.dumps(
            {
                "status": "completed",
                "question": None,
                "files_changed": ["src/auth.py"],
                "tests_added": ["tests/test_auth.py"],
                "summary": "Built auth endpoint",
                "confidence": "high",
            }
        )

        with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            await invoker.invoke(
                role="backend_engineer",
                task_prompt="Build auth endpoint",
                context=context,
            )
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["model"] == "sonnet"
            assert call_kwargs["prompt"] == "Build auth endpoint"

    @pytest.mark.asyncio
    async def test_invoke_parses_implementation_result(self, invoker, context):
        """Test that invoke() parses the SDK response into the correct contract type."""
        result_data = {
            "status": "completed",
            "question": None,
            "files_changed": ["src/auth.py"],
            "tests_added": ["tests/test_auth.py"],
            "summary": "Built auth endpoint",
            "confidence": "high",
        }
        mock_result = MagicMock()
        mock_result.result = json.dumps(result_data)

        with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            result = await invoker.invoke(
                role="backend_engineer",
                task_prompt="Build auth endpoint",
                context=context,
            )
            assert isinstance(result, ImplementationResult)
            assert result.status == "completed"
            assert result.files_changed == ["src/auth.py"]

    @pytest.mark.asyncio
    async def test_invoke_query_failure_raises(self, invoker, context):
        """Test that SDK errors are wrapped in InvocationError."""
        with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = Exception("SDK connection failed")
            with pytest.raises(InvocationError, match="SDK connection failed"):
                await invoker.invoke(
                    role="backend_engineer",
                    task_prompt="Build auth endpoint",
                    context=context,
                )

    @pytest.mark.asyncio
    async def test_invoke_invalid_json_raises(self, invoker, context):
        """Test that unparseable agent output raises InvocationError."""
        mock_result = MagicMock()
        mock_result.result = "This is not JSON"

        with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            with pytest.raises(InvocationError, match="parse"):
                await invoker.invoke(
                    role="backend_engineer",
                    task_prompt="Build auth endpoint",
                    context=context,
                )
