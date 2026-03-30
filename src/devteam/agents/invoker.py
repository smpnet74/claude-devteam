"""Agent invoker — wraps Claude Agent SDK query() calls.

Builds the correct invocation parameters (model, tools, working directory,
structured output schema) from the agent registry and executes the query.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from devteam.agents.contracts import (
    DecompositionResult,
    ImplementationResult,
    ReviewResult,
    RoutingResult,
)
from devteam.agents.registry import AgentRegistry

if TYPE_CHECKING:
    from claude_agent_sdk import ResultMessage as _ResultMessage

logger = logging.getLogger(__name__)


class InvocationError(Exception):
    """Raised when an agent invocation fails."""


@dataclass(frozen=True)
class InvocationContext:
    """Runtime context for an agent invocation."""

    worktree_path: Path
    project_name: str
    timeout: float = 300.0


@dataclass(frozen=True)
class QueryOptions:
    """Mirrors ClaudeAgentOptions from the Claude Agent SDK.

    Defined locally so we can code against the correct API shape and verify
    it in tests even when the SDK is not installed.
    """

    model: str = ""
    system_prompt: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    permission_mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] = "default"
    cwd: str | None = None
    output_format: dict[str, Any] | None = None


# Mapping from role slug patterns to their output contract.
# The orchestrator uses this to determine which JSON schema to require.
_ROLE_SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "ceo": RoutingResult,
    "chief_architect": DecompositionResult,
    # TODO(phase-3): Planners produce research reports, not implementations.
    # Create a ResearchResult contract in Phase 3 and update this mapping.
    "planner_researcher_a": ImplementationResult,
    "planner_researcher_b": ImplementationResult,
    "em_team_a": ReviewResult,
    "em_team_b": ReviewResult,
    "qa_engineer": ReviewResult,
    "security_engineer": ReviewResult,
    "tech_writer": ReviewResult,
}

# All engineer roles use ImplementationResult
_ENGINEER_ROLES = {
    "backend_engineer",
    "frontend_engineer",
    "devops_engineer",
    "data_engineer",
    "infra_engineer",
    "tooling_engineer",
    "cloud_engineer",
}


async def _run_query(prompt: str, options: QueryOptions, timeout: float = 300.0) -> _ResultMessage:
    """Execute the Claude Agent SDK query and return the final ResultMessage.

    The SDK's query() returns an AsyncIterator of messages. We consume the
    stream and return the last ResultMessage.

    Note: QueryOptions is our local mirror of ClaudeAgentOptions. We cast to
    Any at the SDK boundary so pyright does not complain about structural
    type mismatches.

    # Note: At runtime, QueryOptions fields map to ClaudeAgentOptions as follows:
    # QueryOptions.model -> ClaudeAgentOptions.model
    # QueryOptions.system_prompt -> ClaudeAgentOptions.system_prompt
    # QueryOptions.allowed_tools -> ClaudeAgentOptions.allowed_tools
    # QueryOptions.permission_mode -> ClaudeAgentOptions.permission_mode
    # QueryOptions.cwd -> ClaudeAgentOptions.cwd
    # QueryOptions.output_format -> ClaudeAgentOptions.output_format
    # The _run_query method handles this translation.

    Args:
        prompt: The prompt to send to the SDK.
        options: Query options for the SDK call.
        timeout: Maximum seconds to wait for the SDK call to complete.

    Raises:
        InvocationError: If the query times out or returns no ResultMessage.
    """
    from claude_agent_sdk import ResultMessage, query

    async def _consume_stream() -> ResultMessage:
        result_msg: ResultMessage | None = None
        from claude_agent_sdk.types import ClaudeAgentOptions

        sdk_options = ClaudeAgentOptions(
            model=options.model or None,
            system_prompt=options.system_prompt or None,
            allowed_tools=options.allowed_tools,
            permission_mode=options.permission_mode
            if options.permission_mode != "default"
            else None,
            cwd=options.cwd,
            output_format=options.output_format,
        )
        async for message in query(prompt=prompt, options=sdk_options):
            if isinstance(message, ResultMessage):
                result_msg = message

        if result_msg is None:
            raise InvocationError("Agent query returned no ResultMessage")
        return result_msg

    try:
        return await asyncio.wait_for(_consume_stream(), timeout=timeout)
    except asyncio.TimeoutError:
        raise InvocationError(f"Agent query timed out after {timeout} seconds") from None


class AgentInvoker:
    """Invokes agents via the Claude Agent SDK with correct parameters.

    Reads model, tools, and prompt from the AgentRegistry. Determines
    the structured output schema based on the role. Wraps query() calls
    with error handling.
    """

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def _get_schema_for_role(self, role: str) -> type[BaseModel]:
        """Return the Pydantic model class for a role's output schema.

        Raises:
            InvocationError: If no schema is mapped for the given role.
        """
        if role in _ROLE_SCHEMA_MAP:
            return _ROLE_SCHEMA_MAP[role]
        if role in _ENGINEER_ROLES:
            return ImplementationResult
        raise InvocationError(
            f"No output schema mapped for role '{role}'. Add it to _ROLE_SCHEMA_MAP."
        )

    def schema_for_role(self, role: str) -> dict[str, Any]:
        """Return the JSON schema for a role's structured output.

        Args:
            role: Agent role slug.

        Returns:
            JSON schema dict suitable for the Agent SDK's output_format parameter.

        Raises:
            InvocationError: If no schema is mapped for the role.
        """
        return self._get_schema_for_role(role).model_json_schema()

    def build_query_params(
        self,
        role: str,
        task_prompt: str,
        context: InvocationContext,
    ) -> dict[str, Any]:
        """Build the parameter dict for a Claude Agent SDK query() call.

        Args:
            role: Agent role slug (must exist in registry).
            task_prompt: The task-specific prompt to send to the agent.
            context: Runtime context (worktree path, project name).

        Returns:
            Dict with ``prompt`` and ``options`` keys matching the SDK's
            ``query(prompt=..., options=ClaudeAgentOptions(...))`` signature.

        Raises:
            KeyError: If role is not in the registry.
            InvocationError: If no schema is mapped for the role.
        """
        defn = self._registry.get(role)
        schema_cls = self._get_schema_for_role(role)

        options = QueryOptions(
            model=defn.model,
            system_prompt=defn.prompt,
            allowed_tools=list(defn.tools),
            permission_mode="default",
            cwd=str(context.worktree_path),
            output_format={
                "type": "json_schema",
                "schema": schema_cls.model_json_schema(),
            },
        )

        return {"prompt": task_prompt, "options": options}

    async def invoke(
        self,
        role: str,
        task_prompt: str,
        context: InvocationContext,
    ) -> BaseModel:
        """Invoke an agent and return the parsed structured result.

        Args:
            role: Agent role slug.
            task_prompt: The task-specific prompt.
            context: Runtime context.

        Returns:
            Parsed Pydantic model instance (ImplementationResult, ReviewResult, etc.).

        Raises:
            InvocationError: If the SDK call fails or the result cannot be parsed.
            KeyError: If the role is not in the registry.
        """
        params = self.build_query_params(role, task_prompt, context)

        logger.info(
            "Invoking agent '%s' (model=%s) for project '%s'",
            role,
            params["options"].model,
            context.project_name,
        )

        try:
            sdk_result = await _run_query(
                prompt=params["prompt"],
                options=params["options"],
                timeout=context.timeout,
            )
        except InvocationError:
            raise
        except Exception as e:
            raise InvocationError(f"Agent '{role}' invocation failed: {e}") from e

        # Check for agent errors before parsing
        if hasattr(sdk_result, "is_error") and sdk_result.is_error:
            raise InvocationError(
                f"Agent '{role}' returned an error: {getattr(sdk_result, 'result', 'unknown error')}"
            )

        # Parse the structured JSON output
        result_type = self._get_schema_for_role(role)
        try:
            # Prefer structured_output if available
            if (
                hasattr(sdk_result, "structured_output")
                and sdk_result.structured_output is not None
            ):
                raw_data = sdk_result.structured_output
            else:
                raw_data = json.loads(sdk_result.result)  # type: ignore[arg-type]
            return result_type.model_validate(raw_data)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            raise InvocationError(f"Failed to parse agent '{role}' output: {e}") from e
