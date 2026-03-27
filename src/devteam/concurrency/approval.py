"""Configurable approval gates for side-effecting actions.

Each action (commit, push, open_pr, merge, cleanup) has a policy:
- auto: proceed without human intervention
- manual: pause and wait for human approval
- never: hard block, action is forbidden

push_to_main is always "never" regardless of configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ApprovalPolicy(Enum):
    """Policy for a side-effecting action."""

    AUTO = "auto"
    MANUAL = "manual"
    NEVER = "never"

    def is_auto(self) -> bool:
        return self == ApprovalPolicy.AUTO

    def is_manual(self) -> bool:
        return self == ApprovalPolicy.MANUAL

    def is_never(self) -> bool:
        return self == ApprovalPolicy.NEVER

    @classmethod
    def from_string(cls, s: str) -> ApprovalPolicy:
        key = s.strip().lower()
        for member in cls:
            if member.value == key:
                return member
        raise ValueError(f"Invalid approval policy '{s}'. Must be one of: auto, manual, never")


@dataclass
class ApprovalGates:
    """Approval policies for all side-effecting actions."""

    commit: ApprovalPolicy
    push: ApprovalPolicy
    open_pr: ApprovalPolicy
    merge: ApprovalPolicy
    cleanup: ApprovalPolicy
    push_to_main: ApprovalPolicy


@dataclass
class ApprovalDecision:
    """Result of checking an approval gate."""

    approved: bool
    needs_human: bool
    blocked: bool
    action: str
    policy: ApprovalPolicy


# Spec defaults from config.toml
DEFAULT_GATES = ApprovalGates(
    commit=ApprovalPolicy.AUTO,
    push=ApprovalPolicy.AUTO,
    open_pr=ApprovalPolicy.AUTO,
    merge=ApprovalPolicy.AUTO,
    cleanup=ApprovalPolicy.AUTO,
    push_to_main=ApprovalPolicy.NEVER,
)

# Actions that map to ApprovalGates fields
VALID_ACTIONS = {"commit", "push", "open_pr", "merge", "cleanup", "push_to_main"}


def load_approval_gates(config: dict[str, Any]) -> ApprovalGates:
    """Load approval gates from a config dict (parsed from config.toml).

    Missing keys fall back to defaults. push_to_main is always forced
    to NEVER regardless of what the config says.
    """
    approval_section = config.get("approval", {})

    def _get_policy(key: str, default: ApprovalPolicy) -> ApprovalPolicy:
        value = approval_section.get(key)
        if value is None:
            return default
        return ApprovalPolicy.from_string(value)

    gates = ApprovalGates(
        commit=_get_policy("commit", DEFAULT_GATES.commit),
        push=_get_policy("push", DEFAULT_GATES.push),
        open_pr=_get_policy("open_pr", DEFAULT_GATES.open_pr),
        merge=_get_policy("merge", DEFAULT_GATES.merge),
        cleanup=_get_policy("cleanup", DEFAULT_GATES.cleanup),
        push_to_main=ApprovalPolicy.NEVER,  # ALWAYS never, hard block
    )
    return gates


def check_approval(gates: ApprovalGates, action: str) -> ApprovalDecision:
    """Check whether an action is approved, needs human approval, or is blocked.

    Args:
        gates: The current approval gate configuration.
        action: One of: commit, push, open_pr, merge, cleanup, push_to_main.

    Returns:
        ApprovalDecision with the verdict.

    Raises:
        ValueError: If action is not a recognized gate.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(
            f"Unknown action '{action}'. Must be one of: {', '.join(sorted(VALID_ACTIONS))}"
        )

    policy: ApprovalPolicy = getattr(gates, action)

    if policy.is_auto():
        return ApprovalDecision(
            approved=True,
            needs_human=False,
            blocked=False,
            action=action,
            policy=policy,
        )
    elif policy.is_manual():
        return ApprovalDecision(
            approved=False,
            needs_human=True,
            blocked=False,
            action=action,
            policy=policy,
        )
    else:  # NEVER
        return ApprovalDecision(
            approved=False,
            needs_human=False,
            blocked=True,
            action=action,
            policy=policy,
        )
