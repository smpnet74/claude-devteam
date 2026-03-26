"""Agent definitions, registry, invoker, and structured output contracts."""

from devteam.agents.contracts import (
    DecompositionResult,
    ImplementationResult,
    ReviewComment,
    ReviewResult,
    RoutingResult,
    TaskDecomposition,
)
from devteam.agents.invoker import AgentInvoker, InvocationContext, InvocationError
from devteam.agents.registry import AgentDefinition, AgentRegistry
from devteam.agents.template_manager import (
    copy_agent_templates,
    copy_agents_to_project,
    get_bundled_templates_dir,
)

__all__ = [
    # Contracts
    "DecompositionResult",
    "ImplementationResult",
    "ReviewComment",
    "ReviewResult",
    "RoutingResult",
    "TaskDecomposition",
    # Invoker
    "AgentInvoker",
    "InvocationContext",
    "InvocationError",
    # Registry
    "AgentDefinition",
    "AgentRegistry",
    # Template management
    "copy_agent_templates",
    "copy_agents_to_project",
    "get_bundled_templates_dir",
]
