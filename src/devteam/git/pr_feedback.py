"""PR feedback loop -- session resumption, diff-only feedback, circuit breaker.

This module contains the pure logic for the feedback loop. The actual
DBOS workflow orchestration (polling, sleep, agent invocation) lives in
the workflow layer -- this module provides the building blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from devteam.git.pr import PRFeedback


class FeedbackLoopOutcome(Enum):
    """Result of the entire feedback loop."""

    ALL_GREEN = "all_green"
    CIRCUIT_BREAKER = "circuit_breaker"
    ESCALATED = "escalated"


@dataclass
class FeedbackLoopConfig:
    """Configuration for the PR feedback loop."""

    max_iterations: int = 5
    poll_interval_seconds: int = 60


@dataclass
class FeedbackIteration:
    """Record of one feedback-fix iteration."""

    iteration: int
    feedback: PRFeedback
    session_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FeedbackLoopResult:
    """Final result of the feedback loop."""

    outcome: FeedbackLoopOutcome
    iterations: list[FeedbackIteration] = field(default_factory=list)
    total_iterations: int = 0


def build_feedback_prompt(
    feedback: PRFeedback,
    iteration: int,
    max_iterations: int,
) -> str:
    """Build a prompt for the engineer to fix PR feedback.

    Structures feedback by priority: errors > warnings > nitpicks.
    Includes iteration count so the agent knows how many attempts remain.

    Args:
        feedback: Current PR feedback state.
        iteration: Current iteration number (1-based).
        max_iterations: Maximum allowed iterations.

    Returns:
        Formatted prompt string for the agent.
    """
    sections: list[str] = []

    sections.append(
        f"## PR Fix Iteration {iteration}/{max_iterations}\n"
        f"This is fix attempt {iteration} of {max_iterations}. "
        f"Focus on the highest-severity issues first."
    )

    # Failed CI checks
    if feedback.failed_checks:
        checks_str = "\n".join(f"  - {c}" for c in feedback.failed_checks)
        sections.append(f"### Failed CI Checks\n{checks_str}")

    # CodeRabbit comments -- errors first
    cr = feedback.coderabbit_comments
    if cr.errors:
        errors_str = "\n".join(f"  - {e}" for e in cr.errors)
        sections.append(f"### CodeRabbit Errors (must fix)\n{errors_str}")

    if cr.warnings:
        warnings_str = "\n".join(f"  - {w}" for w in cr.warnings)
        sections.append(f"### CodeRabbit Warnings (should fix)\n{warnings_str}")

    if cr.nitpicks:
        nitpicks_str = "\n".join(f"  - {n}" for n in cr.nitpicks)
        sections.append(f"### CodeRabbit Nitpicks (low priority)\n{nitpicks_str}")

    # Review comments from humans
    if feedback.review_comments:
        comments_str = "\n".join(f"  - {r.get('body', str(r))}" for r in feedback.review_comments)
        sections.append(f"### Review Comments\n{comments_str}")

    return "\n\n".join(sections)


def filter_new_feedback(
    comments: list[dict[str, Any]],
    since: datetime | None,
) -> list[dict[str, Any]]:
    """Filter comments to only those newer than a cutoff.

    Enables diff-only feedback: each iteration only shows NEW
    failures and comments, not everything from the beginning.

    Args:
        comments: List of comment dicts with optional 'createdAt' key.
        since: Only include comments after this timestamp. If None,
               return all comments.

    Returns:
        Filtered list of comments.
    """
    if since is None:
        return comments

    result = []
    for comment in comments:
        created_str = comment.get("createdAt")
        if created_str is None:
            result.append(comment)
            continue
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if created > since:
                result.append(comment)
        except (ValueError, AttributeError):
            result.append(comment)
    return result


def should_continue_loop(
    iteration: int,
    all_green: bool,
    config: FeedbackLoopConfig,
) -> bool:
    """Decide whether to continue the feedback loop.

    Args:
        iteration: Current iteration number (1-based).
        all_green: Whether all checks and reviews pass.
        config: Loop configuration (max iterations, etc.).

    Returns:
        True if the loop should continue (more fixes needed),
        False if it should stop (success or circuit breaker).
    """
    if all_green:
        return False
    if iteration >= config.max_iterations:
        return False
    return True
