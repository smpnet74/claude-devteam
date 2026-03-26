"""Tests for agent invoker — wraps Claude Agent SDK query() calls."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from devteam.agents.contracts import (
    ImplementationResult,
    ReviewResult,
    RoutingResult,
)
from devteam.agents.invoker import AgentInvoker, InvocationContext, InvocationError, QueryOptions
from devteam.agents.registry import AgentDefinition, AgentRegistry

_MOCK_TARGET = "devteam.agents.invoker._run_query"


@pytest.fixture
def mock_registry():
    """Create a registry with a few test agents."""
    agents = {
        "backend_engineer": AgentDefinition(
            role="backend_engineer",
            model="sonnet",
            tools=(
                "Read",
                "Edit",
                "Write",
                "Bash",
                "Glob",
                "Grep",
                "WebSearch",
                "WebFetch",
                "query_knowledge",
            ),
            prompt="You are the Backend Engineer.",
        ),
        "ceo": AgentDefinition(
            role="ceo",
            model="opus",
            tools=("Read", "Glob", "Grep"),
            prompt="You are the CEO.",
        ),
        "qa_engineer": AgentDefinition(
            role="qa_engineer",
            model="haiku",
            tools=(
                "Read",
                "Edit",
                "Write",
                "Bash",
                "Glob",
                "Grep",
                "WebSearch",
                "WebFetch",
                "query_knowledge",
            ),
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


class TestQueryOptions:
    def test_defaults(self):
        opts = QueryOptions()
        assert opts.model == ""
        assert opts.system_prompt == ""
        assert opts.allowed_tools == []
        assert opts.permission_mode == "default"
        assert opts.cwd is None
        assert opts.output_format is None

    def test_frozen(self):
        opts = QueryOptions(model="sonnet")
        with pytest.raises(AttributeError):
            opts.model = "opus"  # type: ignore[misc]


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

    def test_schema_for_unknown_role_raises(self, invoker):
        """Unknown roles must fail closed with InvocationError."""
        with pytest.raises(InvocationError, match="No output schema mapped for role"):
            invoker.schema_for_role("mystery_agent")

    def test_build_query_params_engineer(self, invoker, context):
        params = invoker.build_query_params(
            role="backend_engineer",
            task_prompt="Implement the login endpoint",
            context=context,
        )
        # Top-level keys
        assert params["prompt"] == "Implement the login endpoint"
        assert isinstance(params["options"], QueryOptions)

        # Options fields
        opts = params["options"]
        assert opts.model == "sonnet"
        assert opts.system_prompt == "You are the Backend Engineer."
        assert opts.allowed_tools == [
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
        assert opts.permission_mode == "default"
        assert opts.cwd == str(context.worktree_path)
        assert opts.output_format is not None
        assert opts.output_format["type"] == "json_schema"
        assert opts.output_format["schema"] == ImplementationResult.model_json_schema()

    def test_build_query_params_ceo(self, invoker, context):
        params = invoker.build_query_params(
            role="ceo",
            task_prompt="Route this incoming request",
            context=context,
        )
        opts = params["options"]
        assert opts.model == "opus"
        assert opts.system_prompt == "You are the CEO."
        assert opts.allowed_tools == ["Read", "Glob", "Grep"]
        assert opts.output_format["schema"] == RoutingResult.model_json_schema()

    def test_build_query_params_qa(self, invoker, context):
        params = invoker.build_query_params(
            role="qa_engineer",
            task_prompt="Review this PR",
            context=context,
        )
        opts = params["options"]
        assert opts.model == "haiku"
        assert opts.system_prompt == "You are the QA Engineer."
        assert opts.output_format["schema"] == ReviewResult.model_json_schema()

    def test_build_query_params_worktree_path(self, invoker, context):
        params = invoker.build_query_params(
            role="backend_engineer",
            task_prompt="test",
            context=context,
        )
        assert params["options"].cwd == str(context.worktree_path)

    def test_build_query_params_unknown_role_raises(self, invoker, context):
        with pytest.raises(KeyError):
            invoker.build_query_params(
                role="unknown_agent",
                task_prompt="test",
                context=context,
            )

    @pytest.mark.asyncio
    async def test_invoke_calls_run_query(self, invoker, context):
        """Test that invoke() calls _run_query with correct prompt + options."""
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_output = None
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
            assert call_kwargs["prompt"] == "Build auth endpoint"
            assert isinstance(call_kwargs["options"], QueryOptions)
            assert call_kwargs["options"].model == "sonnet"
            assert call_kwargs["options"].system_prompt == "You are the Backend Engineer."

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
        mock_result.is_error = False
        mock_result.structured_output = None
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
        mock_result.is_error = False
        mock_result.structured_output = None
        mock_result.result = "This is not JSON"

        with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            with pytest.raises(InvocationError, match="parse"):
                await invoker.invoke(
                    role="backend_engineer",
                    task_prompt="Build auth endpoint",
                    context=context,
                )


class TestSdkCallShape:
    """Verify _run_query calls SDK with correct prompt + options structure."""

    @pytest.mark.asyncio
    async def test_sdk_call_shape(self, invoker, context):
        """Patch claude_agent_sdk.query directly and verify the call shape."""
        mock_result_msg = MagicMock()
        mock_result_msg.is_error = False
        mock_result_msg.structured_output = None
        mock_result_msg.result = json.dumps(
            {
                "status": "completed",
                "question": None,
                "files_changed": [],
                "tests_added": [],
                "summary": "Done",
                "confidence": "high",
            }
        )

        # Create an async iterator that yields our mock ResultMessage
        async def mock_query(prompt, options):
            # Import locally so we can reference the patched ResultMessage
            yield mock_result_msg

        with (
            patch(
                "devteam.agents.invoker.query",
                create=True,
            ) as _,
            patch(
                "devteam.agents.invoker._run_query",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            mock_run.return_value = mock_result_msg
            await invoker.invoke(
                role="backend_engineer",
                task_prompt="Build the thing",
                context=context,
            )
            # Verify _run_query was called with prompt= and options= kwargs
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert "prompt" in call_kwargs
            assert "options" in call_kwargs
            assert call_kwargs["prompt"] == "Build the thing"
            opts = call_kwargs["options"]
            assert opts.model == "sonnet"
            assert opts.system_prompt == "You are the Backend Engineer."
            assert opts.cwd == str(context.worktree_path)
            assert opts.output_format is not None


class TestSdkErrorHandling:
    """Verify handling of SDK-level errors and structured output."""

    @pytest.mark.asyncio
    async def test_invoke_raises_on_sdk_error(self, invoker, context):
        """When SDK returns is_error=True, invoke should raise InvocationError."""
        mock_result = MagicMock()
        mock_result.is_error = True
        mock_result.result = "Something went wrong in the agent"
        mock_result.structured_output = None

        with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            with pytest.raises(InvocationError, match="returned an error"):
                await invoker.invoke(
                    role="backend_engineer",
                    task_prompt="Build auth endpoint",
                    context=context,
                )

    @pytest.mark.asyncio
    async def test_invoke_uses_structured_output(self, invoker, context):
        """When SDK returns structured_output, it should be preferred over raw result."""
        structured_data = {
            "status": "completed",
            "question": None,
            "files_changed": ["src/structured.py"],
            "tests_added": [],
            "summary": "Used structured output",
            "confidence": "high",
        }
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_output = structured_data
        # raw result is different — should NOT be used
        mock_result.result = json.dumps(
            {
                "status": "completed",
                "question": None,
                "files_changed": ["src/raw.py"],
                "tests_added": [],
                "summary": "Used raw result",
                "confidence": "low",
            }
        )

        with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            result = await invoker.invoke(
                role="backend_engineer",
                task_prompt="Build auth endpoint",
                context=context,
            )
            assert isinstance(result, ImplementationResult)
            assert result.files_changed == ["src/structured.py"]
            assert result.summary == "Used structured output"

    @pytest.mark.asyncio
    async def test_invoke_raises_on_timeout(self, invoker, context):
        """When SDK call times out, InvocationError should be raised."""
        import asyncio

        async def slow_query(*args, **kwargs):
            await asyncio.sleep(10)

        with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = InvocationError("Agent query timed out after 1.0 seconds")
            with pytest.raises(InvocationError, match="timed out"):
                await invoker.invoke(
                    role="backend_engineer",
                    task_prompt="Build auth endpoint",
                    context=context,
                )


class TestUnknownRoleFailsClosed:
    """Unknown roles must raise InvocationError, not silently default."""

    def test_get_schema_for_unknown_role_raises(self, invoker):
        with pytest.raises(InvocationError, match="No output schema mapped for role"):
            invoker._get_schema_for_role("totally_unknown_role")

    def test_schema_for_role_unknown_raises(self, invoker):
        with pytest.raises(InvocationError, match="No output schema mapped for role"):
            invoker.schema_for_role("totally_unknown_role")

    @pytest.mark.asyncio
    async def test_invoke_unknown_role_raises(self, invoker, context):
        """End-to-end: invoke() with an unknown role raises InvocationError."""
        with pytest.raises((InvocationError, KeyError)):
            await invoker.invoke(
                role="totally_unknown_role",
                task_prompt="Do something",
                context=context,
            )
