"""Concurrency management for claude-devteam.

Provides rate limiting, priority queuing, approval gates, and
durable sleep for coordinating concurrent agent invocations.
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
from devteam.concurrency.durable_sleep import (
    PendingSleep,
    cancel_sleep,
    check_pending_sleep,
    durable_sleep,
    resume_sleep,
)
from devteam.concurrency.invoke import (
    RateLimitError,
    rate_limit_aware_invoke,
)
from devteam.concurrency.priority import (
    Priority,
    prioritize_tasks,
)
from devteam.concurrency.queue import (
    AgentQueueConfig,
    AgentQueueItem,
    create_agent_queue_config,
    dequeue_next,
    enqueue_agent_invocation,
    get_active_count,
    get_queue_depth,
    init_queue_table,
)
from devteam.concurrency.rate_limit import (
    DEFAULT_BACKOFF_SECONDS,
    PauseCheckResult,
    PauseStatus,
    check_pause_before_invoke,
    clear_global_pause,
    get_global_pause,
    handle_rate_limit_error,
    init_pause_table,
    is_paused,
    set_global_pause,
)
from devteam.concurrency.status_display import (
    format_queue_status,
    format_rate_limit_status,
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
    # durable_sleep
    "PendingSleep",
    "cancel_sleep",
    "check_pending_sleep",
    "durable_sleep",
    "resume_sleep",
    # invoke
    "RateLimitError",
    "rate_limit_aware_invoke",
    # priority
    "Priority",
    "prioritize_tasks",
    # queue
    "AgentQueueConfig",
    "AgentQueueItem",
    "create_agent_queue_config",
    "dequeue_next",
    "enqueue_agent_invocation",
    "get_active_count",
    "get_queue_depth",
    "init_queue_table",
    # rate_limit
    "DEFAULT_BACKOFF_SECONDS",
    "PauseCheckResult",
    "PauseStatus",
    "check_pause_before_invoke",
    "clear_global_pause",
    "get_global_pause",
    "handle_rate_limit_error",
    "init_pause_table",
    "is_paused",
    "set_global_pause",
    # status_display
    "format_queue_status",
    "format_rate_limit_status",
]
