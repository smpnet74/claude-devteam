"""Agent definitions, registry, invoker, and structured output contracts."""

from devteam.agents.contracts import (
    DecompositionResult,
    EscalationLevel,
    ImplementationResult,
    QuestionRecord,
    QuestionType,
    ReviewComment,
    ReviewResult,
    RoutePath,
    RoutingResult,
    TaskDecomposition,
    WorkType,
)
from devteam.agents.invoker import AgentInvoker, InvocationContext, InvocationError, QueryOptions
from devteam.agents.registry import AgentDefinition, AgentRegistry
from devteam.agents.template_manager import (
    copy_agent_templates,
    copy_agents_to_project,
    get_bundled_templates_dir,
)

__all__ = [
    # Contracts
    "DecompositionResult",
    "EscalationLevel",
    "ImplementationResult",
    "QuestionRecord",
    "QuestionType",
    "ReviewComment",
    "ReviewResult",
    "RoutePath",
    "RoutingResult",
    "TaskDecomposition",
    "WorkType",
    # Invoker
    "AgentInvoker",
    "InvocationContext",
    "InvocationError",
    "QueryOptions",
    # Registry
    "AgentDefinition",
    "AgentRegistry",
    # Template management
    "copy_agent_templates",
    "copy_agents_to_project",
    "get_bundled_templates_dir",
]
