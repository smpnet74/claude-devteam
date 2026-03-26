"""Agent invoker — wraps Claude Agent SDK query() calls.

Builds the correct invocation parameters (model, tools, working directory,
structured output schema) from the agent registry and executes the query.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

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


# Mapping from role slug patterns to their output contract.
# The orchestrator uses this to determine which JSON schema to require.
_ROLE_SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "ceo": RoutingResult,
    "chief_architect": DecompositionResult,
    "planner_researcher_a": DecompositionResult,
    "planner_researcher_b": DecompositionResult,
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


async def _run_query(**params: Any) -> _ResultMessage:
    """Execute the Claude Agent SDK query and return the final ResultMessage.

    The SDK's query() returns an AsyncIterator of messages. We consume the
    stream and return the last ResultMessage.
    """
    from claude_agent_sdk import ResultMessage, query

    result_msg: ResultMessage | None = None
    async for message in query(**params):
        if isinstance(message, ResultMessage):
            result_msg = message

    if result_msg is None:
        raise InvocationError("Agent query returned no ResultMessage")

    return result_msg


class AgentInvoker:
    """Invokes agents via the Claude Agent SDK with correct parameters.

    Reads model, tools, and prompt from the AgentRegistry. Determines
    the structured output schema based on the role. Wraps query() calls
    with error handling.
    """

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def schema_for_role(self, role: str) -> dict[str, Any]:
        """Return the JSON schema for a role's structured output.

        Args:
            role: Agent role slug.

        Returns:
            JSON schema dict suitable for the Agent SDK's output_format parameter.
        """
        if role in _ROLE_SCHEMA_MAP:
            return _ROLE_SCHEMA_MAP[role].model_json_schema()
        if role in _ENGINEER_ROLES:
            return ImplementationResult.model_json_schema()
        # Default to ImplementationResult for unknown roles
        return ImplementationResult.model_json_schema()

    def _result_type_for_role(self, role: str) -> type[BaseModel]:
        """Return the Pydantic model class for a role's output."""
        if role in _ROLE_SCHEMA_MAP:
            return _ROLE_SCHEMA_MAP[role]
        return ImplementationResult

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
            Dict of keyword arguments for query().

        Raises:
            KeyError: If role is not in the registry.
        """
        defn = self._registry.get(role)

        return {
            "prompt": task_prompt,
            "model": defn.model,
            "agent": role,
            "cwd": str(context.worktree_path),
            "allowed_tools": defn.tools,
            "permission_mode": "bypassPermissions",
            "json_schema": self.schema_for_role(role),
        }

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
            params["model"],
            context.project_name,
        )

        try:
            sdk_result = await _run_query(**params)
        except InvocationError:
            raise
        except Exception as e:
            raise InvocationError(f"Agent '{role}' invocation failed: {e}") from e

        # Parse the structured JSON output
        result_type = self._result_type_for_role(role)
        try:
            data = json.loads(sdk_result.result)  # type: ignore[arg-type]
            return result_type.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            raise InvocationError(f"Failed to parse agent '{role}' output: {e}") from e
