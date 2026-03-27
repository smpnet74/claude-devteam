"""Tests for question escalation workflow."""

import pytest
from unittest.mock import MagicMock

from devteam.orchestrator.escalation import (
    EscalationAttempt,
    attempt_resolution,
    build_escalation_prompt,
    escalate_question,
    get_escalation_path,
    resolve_with_human_answer,
)
from devteam.orchestrator.schemas import (
    EscalationLevel,
    QuestionRecord,
    QuestionType,
)


def _make_question(qtype: QuestionType = QuestionType.TECHNICAL) -> QuestionRecord:
    return QuestionRecord(
        question="Redis or Memcached?",
        question_type=qtype,
        context="Choosing a caching layer for the API",
    )


# ---------------------------------------------------------------------------
# get_escalation_path
# ---------------------------------------------------------------------------


class TestGetEscalationPath:
    def test_architectural_goes_to_ca(self) -> None:
        path = get_escalation_path(QuestionType.ARCHITECTURAL)
        assert "chief_architect" in path

    def test_product_goes_to_ceo(self) -> None:
        path = get_escalation_path(QuestionType.PRODUCT)
        assert "ceo" in path

    def test_process_goes_to_ceo(self) -> None:
        path = get_escalation_path(QuestionType.PROCESS)
        assert "ceo" in path

    def test_technical_stays_at_em(self) -> None:
        path = get_escalation_path(QuestionType.TECHNICAL)
        assert path == ["em"]

    def test_blocked_goes_through_full_chain(self) -> None:
        path = get_escalation_path(QuestionType.BLOCKED)
        assert "em" in path
        assert "chief_architect" in path
        assert "ceo" in path
        assert "human" in path

    def test_all_paths_end_at_human_or_em(self) -> None:
        for qt in QuestionType:
            path = get_escalation_path(qt)
            assert path[-1] in ("em", "human")


# ---------------------------------------------------------------------------
# build_escalation_prompt
# ---------------------------------------------------------------------------


class TestBuildEscalationPrompt:
    def test_includes_question(self) -> None:
        q = _make_question()
        prompt = build_escalation_prompt(q, "em_team_a")
        assert "Redis or Memcached?" in prompt

    def test_includes_question_type(self) -> None:
        q = _make_question(QuestionType.ARCHITECTURAL)
        prompt = build_escalation_prompt(q, "chief_architect")
        assert "architectural" in prompt

    def test_includes_level(self) -> None:
        q = _make_question()
        prompt = build_escalation_prompt(q, "chief_architect")
        assert "chief_architect" in prompt

    def test_includes_context_when_set(self) -> None:
        q = _make_question()
        prompt = build_escalation_prompt(q, "em_team_a")
        assert "Choosing a caching layer for the API" in prompt

    def test_omits_context_when_empty(self) -> None:
        q = QuestionRecord(
            question="Redis or Memcached?",
            question_type=QuestionType.TECHNICAL,
            context="",
        )
        prompt = build_escalation_prompt(q, "em_team_a")
        assert "Context:" not in prompt


# ---------------------------------------------------------------------------
# attempt_resolution
# ---------------------------------------------------------------------------


class TestAttemptResolution:
    def test_resolved(self) -> None:
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "resolved": True,
            "answer": "Use Redis",
            "reasoning": "Better for our use case",
        }
        q = _make_question()
        attempt = attempt_resolution(q, "em_team_a", invoker)

        assert attempt.resolved
        assert attempt.answer == "Use Redis"
        assert attempt.level == "em_team_a"

    def test_not_resolved(self) -> None:
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "resolved": False,
            "reasoning": "Need more context",
        }
        q = _make_question()
        attempt = attempt_resolution(q, "em_team_a", invoker)

        assert not attempt.resolved
        assert attempt.answer is None

    def test_invoker_failure_wrapped(self) -> None:
        invoker = MagicMock()
        invoker.invoke.side_effect = ConnectionError("timeout")
        q = _make_question()

        with pytest.raises(RuntimeError, match="Escalation attempt to 'em_team_a' failed"):
            attempt_resolution(q, "em_team_a", invoker)

    def test_malformed_payload_returns_unresolved(self) -> None:
        """Invoker returns garbage that fails schema validation -> unresolved attempt."""
        invoker = MagicMock()
        invoker.invoke.return_value = {"garbage": "data"}  # missing required fields
        q = _make_question()
        attempt = attempt_resolution(q, "em_team_a", invoker)

        assert not attempt.resolved
        assert "failed schema validation" in (attempt.reasoning or "")

    def test_missing_reasoning_returns_unresolved(self) -> None:
        """Invoker returns dict without reasoning -> returns unresolved."""
        invoker = MagicMock()
        invoker.invoke.return_value = {"resolved": True, "answer": "Use Redis"}
        q = _make_question()
        attempt = attempt_resolution(q, "em_team_a", invoker)

        assert not attempt.resolved
        assert "failed schema validation" in (attempt.reasoning or "")

    def test_attempt_is_frozen(self) -> None:
        attempt = EscalationAttempt(level="em", resolved=True, answer="yes")
        with pytest.raises(AttributeError):
            attempt.level = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# escalate_question
# ---------------------------------------------------------------------------


class TestEscalateQuestion:
    def test_resolved_at_em_level(self) -> None:
        """Technical question resolved by EM."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "resolved": True,
            "answer": "Use Redis for its pub/sub support",
            "reasoning": "Matches our existing stack",
        }
        q = _make_question(QuestionType.TECHNICAL)
        result = escalate_question(q, invoker, em_role="em_team_a")

        assert result.resolved
        assert not result.needs_human
        assert len(result.attempts) == 1
        assert result.final_level == EscalationLevel.SUPERVISOR
        assert result.answer == "Use Redis for its pub/sub support"

    def test_escalated_to_ca(self) -> None:
        """Architecture question: EM can't resolve, CA can."""
        invoker = MagicMock()

        def side_effect(role: str, prompt: str, **kwargs: object) -> dict[str, object]:
            if role == "em_team_a":
                return {"resolved": False, "reasoning": "Need CA input"}
            elif role == "chief_architect":
                return {
                    "resolved": True,
                    "answer": "Use event sourcing",
                    "reasoning": "Matches architecture",
                }
            return {"resolved": False, "reasoning": "Cannot answer"}

        invoker.invoke.side_effect = side_effect
        q = _make_question(QuestionType.ARCHITECTURAL)
        result = escalate_question(q, invoker, em_role="em_team_a")

        assert result.resolved
        assert len(result.attempts) == 2
        assert result.answer == "Use event sourcing"
        assert result.final_level == EscalationLevel.LEADERSHIP

    def test_escalated_to_human(self) -> None:
        """No agent can resolve -> needs human."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "resolved": False,
            "reasoning": "Cannot determine",
        }
        q = _make_question(QuestionType.PRODUCT)
        result = escalate_question(q, invoker, em_role="em_team_a")

        assert not result.resolved
        assert result.needs_human
        assert result.final_level == EscalationLevel.HUMAN

    def test_question_pauses_branch(self) -> None:
        """Unresolved question means the branch stays paused."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "resolved": False,
            "reasoning": "Needs business decision",
        }
        q = _make_question(QuestionType.PROCESS)
        result = escalate_question(q, invoker, em_role="em_team_a")

        assert not result.resolved
        assert result.needs_human
        # The question object should be preserved
        assert result.question.question == "Redis or Memcached?"

    def test_em_role_substitution(self) -> None:
        """Generic 'em' in path is replaced with specific em_role."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "resolved": True,
            "answer": "yes",
            "reasoning": "ok",
        }
        q = _make_question(QuestionType.TECHNICAL)
        escalate_question(q, invoker, em_role="em_team_b")

        # Should have called em_team_b, not "em"
        call_kwargs = invoker.invoke.call_args.kwargs
        assert call_kwargs["role"] == "em_team_b"

    def test_technical_no_human_in_path(self) -> None:
        """Technical questions: if EM can't resolve, path ends (no human)."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "resolved": False,
            "reasoning": "Cannot determine",
        }
        q = _make_question(QuestionType.TECHNICAL)
        result = escalate_question(q, invoker, em_role="em_team_a")

        # Technical path is just ["em"], no "human" endpoint
        # Falls through to the graceful catch-all
        assert not result.resolved
        assert result.needs_human
        assert len(result.attempts) == 1

    def test_invoker_failure_propagates(self) -> None:
        invoker = MagicMock()
        invoker.invoke.side_effect = ConnectionError("timeout")
        q = _make_question(QuestionType.TECHNICAL)

        with pytest.raises(RuntimeError, match="Escalation attempt"):
            escalate_question(q, invoker, em_role="em_team_a")


# ---------------------------------------------------------------------------
# resolve_with_human_answer
# ---------------------------------------------------------------------------


class TestResolveWithHumanAnswer:
    def test_resolves_question(self) -> None:
        q = _make_question()
        result = resolve_with_human_answer(q, "Use Redis")

        assert result.resolved
        assert result.answer == "Use Redis"
        assert result.final_level == EscalationLevel.HUMAN

    def test_preserves_original_question(self) -> None:
        q = _make_question()
        result = resolve_with_human_answer(q, "Use Redis")

        assert result.question.question == "Redis or Memcached?"
        assert result.question.question_type == QuestionType.TECHNICAL

    def test_no_attempts_for_human_resolution(self) -> None:
        q = _make_question()
        result = resolve_with_human_answer(q, "Use Redis")

        assert result.attempts == []
        assert not result.needs_human
