"""Concurrency management for claude-devteam.

Provides approval gates, priority ordering, and CLI priority commands.
"""

from devteam.concurrency.approval import (
    ApprovalDecision,
    ApprovalGates,
    ApprovalPolicy,
    check_approval,
    load_approval_gates,
)
from devteam.concurrency.cli_priority import (
    PrioritizeResult,
    parse_priority_flag,
    prioritize_task,
)
from devteam.concurrency.config import (
    ConcurrencyConfig,
    load_concurrency_config,
)
from devteam.concurrency.priority import (
    Priority,
    prioritize_tasks,
)

__all__ = [
    # approval
    "ApprovalDecision",
    "ApprovalGates",
    "ApprovalPolicy",
    "check_approval",
    "load_approval_gates",
    # cli_priority
    "PrioritizeResult",
    "parse_priority_flag",
    "prioritize_task",
    # config
    "ConcurrencyConfig",
    "load_concurrency_config",
    # priority
    "Priority",
    "prioritize_tasks",
]
