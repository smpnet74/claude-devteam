"""CEO routing workflow — analyzes intake and determines execution path.

The CEO is the entry point for all work. This workflow analyzes the intake
(spec, plan, issue, or prompt) and returns a RoutingResult that determines
the execution path: full_project, research, small_fix, or oss_contribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from devteam.orchestrator.schemas import RoutePath, RoutingResult


class InvokerProtocol(Protocol):
    """Protocol for agent invocation — allows mocking in tests."""

    def invoke(
        self,
        role: str,
        prompt: str,
        *,
        json_schema: dict[str, Any] | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class IntakeContext:
    """Parsed intake from CLI arguments or API request."""

    spec: str | None = None
    plan: str | None = None
    issue_url: str | None = None
    prompt: str | None = None
    repo_path: str | None = None


def classify_intake(ctx: IntakeContext) -> RoutePath | None:
    """Fast-path classification before invoking the CEO.

    Some intake types have deterministic routes that don't need
    CEO reasoning. Returns None if CEO analysis is needed.
    """
    if ctx.spec and ctx.plan:
        return RoutePath.FULL_PROJECT
    if ctx.issue_url and "github.com" in ctx.issue_url:
        # Could be own repo or external — CEO decides full_project vs oss
        return None
    return None


def build_routing_prompt(ctx: IntakeContext) -> str:
    """Build the prompt for CEO routing analysis."""
    parts = ["Analyze the following intake and determine the routing path.\n"]

    if ctx.spec:
        parts.append(f"## Spec\n{ctx.spec}\n")
    if ctx.plan:
        parts.append(f"## Plan\n{ctx.plan}\n")
    if ctx.issue_url:
        parts.append(f"## Issue URL\n{ctx.issue_url}\n")
    if ctx.prompt:
        parts.append(f"## Request\n{ctx.prompt}\n")

    parts.append(
        "## Routing Options\n"
        "- full_project: Has spec+plan or needs full decomposition\n"
        "- research: Research request, deliverable back to human\n"
        "- small_fix: Clear scope, single engineer can handle it\n"
        "- oss_contribution: Contributing to an external open-source project\n"
        "\nReturn the routing path and your reasoning."
    )
    return "\n".join(parts)


def route_intake(
    ctx: IntakeContext,
    invoker: InvokerProtocol,
) -> RoutingResult:
    """Route incoming work through CEO analysis.

    Fast-path: If spec+plan are provided, route directly to full_project
    without invoking the CEO (deterministic path).

    Otherwise: Invoke the CEO agent for intelligent routing.
    """
    # Fast-path for deterministic routes
    fast_path = classify_intake(ctx)
    if fast_path == RoutePath.FULL_PROJECT:
        return RoutingResult(
            path=RoutePath.FULL_PROJECT,
            reasoning="Spec and plan provided — direct to full project workflow",
        )

    # CEO analysis needed
    prompt = build_routing_prompt(ctx)
    result = invoker.invoke(
        role="ceo",
        prompt=prompt,
        json_schema=RoutingResult.model_json_schema(),
    )
    return RoutingResult.model_validate(result)
