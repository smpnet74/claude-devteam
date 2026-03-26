"""Tests for CEO routing workflow."""

from unittest.mock import MagicMock

from devteam.orchestrator.routing import (
    IntakeContext,
    build_routing_prompt,
    classify_intake,
    route_intake,
)
from devteam.orchestrator.schemas import RoutePath, RoutingResult


class TestClassifyIntake:
    def test_spec_and_plan_is_full_project(self):
        ctx = IntakeContext(spec="some spec", plan="some plan")
        assert classify_intake(ctx) == RoutePath.FULL_PROJECT

    def test_issue_url_needs_ceo(self):
        ctx = IntakeContext(issue_url="https://github.com/org/repo/issues/42")
        assert classify_intake(ctx) is None

    def test_prompt_only_needs_ceo(self):
        ctx = IntakeContext(prompt="Fix the login bug")
        assert classify_intake(ctx) is None

    def test_spec_without_plan_needs_ceo(self):
        ctx = IntakeContext(spec="some spec")
        assert classify_intake(ctx) is None

    def test_plan_without_spec_needs_ceo(self):
        ctx = IntakeContext(plan="some plan")
        assert classify_intake(ctx) is None

    def test_empty_context_needs_ceo(self):
        ctx = IntakeContext()
        assert classify_intake(ctx) is None

    def test_non_github_issue_url_needs_ceo(self):
        ctx = IntakeContext(issue_url="https://gitlab.com/org/repo/issues/42")
        assert classify_intake(ctx) is None


class TestBuildRoutingPrompt:
    def test_includes_spec(self):
        ctx = IntakeContext(spec="My spec content")
        prompt = build_routing_prompt(ctx)
        assert "My spec content" in prompt
        assert "## Spec" in prompt

    def test_includes_plan(self):
        ctx = IntakeContext(plan="My plan content")
        prompt = build_routing_prompt(ctx)
        assert "My plan content" in prompt
        assert "## Plan" in prompt

    def test_includes_issue_url(self):
        ctx = IntakeContext(issue_url="https://github.com/org/repo/issues/1")
        prompt = build_routing_prompt(ctx)
        assert "https://github.com/org/repo/issues/1" in prompt
        assert "## Issue URL" in prompt

    def test_includes_prompt_text(self):
        ctx = IntakeContext(prompt="Fix the login page")
        prompt = build_routing_prompt(ctx)
        assert "Fix the login page" in prompt
        assert "## Request" in prompt

    def test_includes_routing_options(self):
        ctx = IntakeContext(prompt="do something")
        prompt = build_routing_prompt(ctx)
        assert "full_project" in prompt
        assert "research" in prompt
        assert "small_fix" in prompt
        assert "oss_contribution" in prompt

    def test_includes_all_provided_fields(self):
        ctx = IntakeContext(
            spec="spec text",
            plan="plan text",
            issue_url="https://github.com/org/repo/issues/1",
            prompt="extra context",
        )
        prompt = build_routing_prompt(ctx)
        assert "## Spec" in prompt
        assert "## Plan" in prompt
        assert "## Issue URL" in prompt
        assert "## Request" in prompt

    def test_omits_none_fields(self):
        ctx = IntakeContext(prompt="just a prompt")
        prompt = build_routing_prompt(ctx)
        assert "## Spec" not in prompt
        assert "## Plan" not in prompt
        assert "## Issue URL" not in prompt


class TestRouteIntake:
    def test_fast_path_spec_and_plan(self):
        """Spec+plan bypasses CEO entirely."""
        invoker = MagicMock()
        ctx = IntakeContext(spec="spec", plan="plan")
        result = route_intake(ctx, invoker)

        assert result.path == RoutePath.FULL_PROJECT
        assert "Spec and plan provided" in result.reasoning
        invoker.invoke.assert_not_called()

    def test_issue_invokes_ceo(self):
        """Issue URL requires CEO analysis."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "path": "oss_contribution",
            "reasoning": "External repo, no push access",
        }
        ctx = IntakeContext(issue_url="https://github.com/other/repo/issues/5")
        result = route_intake(ctx, invoker)

        assert result.path == RoutePath.OSS_CONTRIBUTION
        invoker.invoke.assert_called_once()

    def test_prompt_invokes_ceo_small_fix(self):
        """Simple prompt routed as small fix by CEO."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "path": "small_fix",
            "reasoning": "Single-file typo fix",
            "target_team": "a",
        }
        ctx = IntakeContext(prompt="Fix typo in README")
        result = route_intake(ctx, invoker)

        assert result.path == RoutePath.SMALL_FIX
        assert result.target_team == "a"

    def test_prompt_invokes_ceo_research(self):
        """Research request recognized by CEO."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "path": "research",
            "reasoning": "User wants analysis, not code changes",
        }
        ctx = IntakeContext(prompt="Research best auth strategies for our stack")
        result = route_intake(ctx, invoker)

        assert result.path == RoutePath.RESEARCH

    def test_ceo_receives_correct_schema(self):
        """Verify the CEO is invoked with the RoutingResult JSON schema."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "path": "full_project",
            "reasoning": "Needs full decomposition",
        }
        ctx = IntakeContext(prompt="Build a new feature")
        route_intake(ctx, invoker)

        call_kwargs = invoker.invoke.call_args
        assert call_kwargs.kwargs["json_schema"] == RoutingResult.model_json_schema()

    def test_ceo_receives_correct_role(self):
        """Verify the CEO agent is invoked with role='ceo'."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "path": "research",
            "reasoning": "test",
        }
        ctx = IntakeContext(prompt="test prompt")
        route_intake(ctx, invoker)

        call_kwargs = invoker.invoke.call_args.kwargs
        assert call_kwargs["role"] == "ceo"
