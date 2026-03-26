"""Tests for task workflow -- review chain enforcement and revision loops."""

import pytest
from unittest.mock import MagicMock

from devteam.models.entities import TaskStatus
from devteam.orchestrator.task_workflow import (
    TaskContext,
    _build_revision_feedback,
    build_implementation_prompt,
    build_review_prompt,
    em_review,
    engineer_execute,
    execute_task_workflow,
    peer_review,
)
from devteam.orchestrator.schemas import (
    ImplementationResult,
    ReviewComment,
    ReviewResult,
    TaskDecomposition,
)


def _make_task(
    task_id: str = "T-1",
    assigned_to: str = "backend_engineer",
) -> TaskDecomposition:
    return TaskDecomposition(
        id=task_id,
        description="Build API endpoint",
        assigned_to=assigned_to,
        team="a",
        pr_group="feat/api",
    )


def _make_ctx(
    task_id: str = "T-1",
    assigned_to: str = "backend_engineer",
    peer: str = "frontend_engineer",
    em: str = "em_team_a",
) -> TaskContext:
    task = _make_task(task_id=task_id, assigned_to=assigned_to)
    return TaskContext(
        task=task,
        peer_reviewer=peer,
        em_role=em,
        worktree_path="/tmp/worktree",
        job_id="W-1",
    )


def _impl_result(status: str = "completed", question: str | None = None) -> dict[str, object]:
    return {
        "status": status,
        "question": question,
        "files_changed": ["src/api.py"],
        "tests_added": ["tests/test_api.py"],
        "summary": "Built the API",
        "confidence": "high",
    }


def _review_result(verdict: str = "approved") -> dict[str, object]:
    base: dict[str, object] = {
        "verdict": verdict,
        "summary": "Looks good" if verdict == "approved" else "Needs work",
    }
    if verdict in ("needs_revision", "approved_with_comments", "blocked"):
        base["comments"] = [
            {"file": "src/api.py", "line": 10, "severity": "warning", "comment": "Issue found"}
        ]
    else:
        base["comments"] = []
    return base


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


class TestBuildImplementationPrompt:
    def test_includes_task_description(self) -> None:
        ctx = _make_ctx()
        prompt = build_implementation_prompt(ctx)
        assert "Build API endpoint" in prompt

    def test_includes_spec_context(self) -> None:
        ctx = _make_ctx()
        ctx.spec_context = "REST API with JWT auth"
        prompt = build_implementation_prompt(ctx)
        assert "REST API with JWT auth" in prompt

    def test_includes_feedback(self) -> None:
        ctx = _make_ctx()
        ctx.feedback = "Use PostgreSQL, not SQLite"
        prompt = build_implementation_prompt(ctx)
        assert "Use PostgreSQL" in prompt

    def test_includes_revision_feedback(self) -> None:
        ctx = _make_ctx()
        prompt = build_implementation_prompt(ctx, revision_feedback="Fix the tests")
        assert "Fix the tests" in prompt
        assert "Revision Required" in prompt

    def test_omits_optional_sections(self) -> None:
        ctx = _make_ctx()
        prompt = build_implementation_prompt(ctx)
        assert "Spec Context" not in prompt
        assert "Operator Feedback" not in prompt
        assert "Revision Required" not in prompt


class TestBuildReviewPrompt:
    def test_includes_task_and_impl(self) -> None:
        task = _make_task()
        impl = ImplementationResult.model_validate(_impl_result())
        prompt = build_review_prompt(task, impl, "Peer Review")
        assert "Build API endpoint" in prompt
        assert "Built the API" in prompt
        assert "src/api.py" in prompt
        assert "Peer Review" in prompt


# ---------------------------------------------------------------------------
# Individual step functions
# ---------------------------------------------------------------------------


class TestEngineerExecute:
    def test_returns_implementation_result(self) -> None:
        invoker = MagicMock()
        invoker.invoke.return_value = _impl_result()
        ctx = _make_ctx()
        result = engineer_execute(ctx, invoker)
        assert isinstance(result, ImplementationResult)
        assert result.status == "completed"

    def test_invoker_failure_wrapped(self) -> None:
        invoker = MagicMock()
        invoker.invoke.side_effect = ConnectionError("timeout")
        ctx = _make_ctx()
        with pytest.raises(RuntimeError, match="Engineer execution failed"):
            engineer_execute(ctx, invoker)


class TestPeerReview:
    def test_returns_review_result(self) -> None:
        invoker = MagicMock()
        invoker.invoke.return_value = _review_result("approved")
        ctx = _make_ctx()
        impl = ImplementationResult.model_validate(_impl_result())
        result = peer_review(ctx, impl, invoker)
        assert isinstance(result, ReviewResult)
        assert result.verdict == "approved"

    def test_invoker_failure_wrapped(self) -> None:
        invoker = MagicMock()
        invoker.invoke.side_effect = ConnectionError("timeout")
        ctx = _make_ctx()
        impl = ImplementationResult.model_validate(_impl_result())
        with pytest.raises(RuntimeError, match="Peer review invocation failed"):
            peer_review(ctx, impl, invoker)


class TestEMReview:
    def test_returns_review_result(self) -> None:
        invoker = MagicMock()
        invoker.invoke.return_value = _review_result("approved")
        ctx = _make_ctx()
        impl = ImplementationResult.model_validate(_impl_result())
        pr = ReviewResult.model_validate(_review_result("approved"))
        result = em_review(ctx, impl, pr, invoker)
        assert isinstance(result, ReviewResult)

    def test_includes_peer_review_in_prompt(self) -> None:
        invoker = MagicMock()
        invoker.invoke.return_value = _review_result("approved")
        ctx = _make_ctx()
        impl = ImplementationResult.model_validate(_impl_result())
        pr = ReviewResult.model_validate(_review_result("approved"))
        em_review(ctx, impl, pr, invoker)

        call_args = invoker.invoke.call_args
        prompt = call_args.kwargs.get(
            "prompt", call_args.args[1] if len(call_args.args) > 1 else ""
        )
        assert "Peer Review Verdict" in prompt

    def test_invoker_failure_wrapped(self) -> None:
        invoker = MagicMock()
        invoker.invoke.side_effect = ConnectionError("timeout")
        ctx = _make_ctx()
        impl = ImplementationResult.model_validate(_impl_result())
        pr = ReviewResult.model_validate(_review_result("approved"))
        with pytest.raises(RuntimeError, match="EM review invocation failed"):
            em_review(ctx, impl, pr, invoker)


# ---------------------------------------------------------------------------
# Review chain enforcement
# ---------------------------------------------------------------------------


class TestReviewChainEnforcement:
    def test_peer_review_called_before_em(self) -> None:
        """The core invariant: peer review MUST happen before EM review."""
        invoker = MagicMock()
        call_order: list[str] = []

        def track_invoke(role: str, prompt: str, **kwargs: object) -> dict[str, object]:
            call_order.append(role)
            if role == "backend_engineer":
                return _impl_result()
            elif role == "frontend_engineer":
                return _review_result("approved")
            elif role == "em_team_a":
                return _review_result("approved")
            raise ValueError(f"Unexpected role: {role}")

        invoker.invoke.side_effect = track_invoke
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.APPROVED
        # Verify order: engineer -> peer -> em
        assert call_order == ["backend_engineer", "frontend_engineer", "em_team_a"]

    def test_happy_path_approved(self) -> None:
        """Clean execution: implement, peer approves, EM approves."""
        invoker = MagicMock()
        invoker.invoke.side_effect = [
            _impl_result(),
            _review_result("approved"),
            _review_result("approved"),
        ]
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.APPROVED
        assert result.revision_count == 0
        assert result.implementation is not None
        assert result.peer_review is not None
        assert result.em_review is not None


# ---------------------------------------------------------------------------
# Revision loop
# ---------------------------------------------------------------------------


class TestRevisionLoop:
    def test_em_rejection_triggers_revision(self) -> None:
        """EM rejects -> engineer re-implements -> reviews again."""
        invoker = MagicMock()
        invoker.invoke.side_effect = [
            # First iteration
            _impl_result(),  # engineer
            _review_result("approved"),  # peer
            _review_result("needs_revision"),  # EM rejects
            # Second iteration
            _impl_result(),  # engineer re-implements
            _review_result("approved"),  # peer
            _review_result("approved"),  # EM approves
        ]
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.APPROVED
        assert result.revision_count == 1

    def test_peer_block_skips_em(self) -> None:
        """Peer blocks -> no EM review, goes straight to revision."""
        invoker = MagicMock()
        call_order: list[str] = []

        def track_invoke(role: str, prompt: str, **kwargs: object) -> dict[str, object]:
            call_order.append(role)
            if role == "backend_engineer":
                return _impl_result()
            elif role == "frontend_engineer":
                if len([c for c in call_order if c == "frontend_engineer"]) == 1:
                    return _review_result("blocked")
                return _review_result("approved")
            elif role == "em_team_a":
                return _review_result("approved")
            raise ValueError(f"Unexpected role: {role}")

        invoker.invoke.side_effect = track_invoke
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        # EM should not be called during first iteration (peer blocked)
        first_block_index = call_order.index("frontend_engineer")
        # After peer block, engineer re-executes before EM is ever called
        assert call_order[first_block_index + 1] == "backend_engineer"
        assert result.status == TaskStatus.APPROVED

    def test_max_revisions_circuit_breaker(self) -> None:
        """After max revisions, task fails instead of looping forever."""
        invoker = MagicMock()
        invoker.invoke.side_effect = [
            # iteration 0
            _impl_result(),
            _review_result("approved"),
            _review_result("needs_revision"),
            # iteration 1
            _impl_result(),
            _review_result("approved"),
            _review_result("needs_revision"),
            # iteration 2
            _impl_result(),
            _review_result("approved"),
            _review_result("needs_revision"),
            # iteration 3 (max_revisions=3, so this is the 4th attempt = index 3)
            _impl_result(),
            _review_result("approved"),
            _review_result("needs_revision"),
        ]
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker, max_revisions=3)

        assert result.status == TaskStatus.FAILED
        assert result.error is not None
        assert "revision iterations" in result.error


# ---------------------------------------------------------------------------
# Question escalation
# ---------------------------------------------------------------------------


class TestQuestionEscalation:
    def test_question_pauses_task_when_escalation_needs_human(self) -> None:
        """Engineer raises question, escalation cannot resolve -> task pauses."""
        invoker = MagicMock()
        call_count = 0

        def side_effect(role: str, prompt: str, **kwargs: object) -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if role == "backend_engineer":
                return _impl_result(
                    status="needs_clarification",
                    question="JWT or sessions?",
                )
            # Escalation: EM cannot resolve
            return {"resolved": False, "reasoning": "Need product decision"}

        invoker.invoke.side_effect = side_effect
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.WAITING_ON_QUESTION
        assert result.question is not None
        assert "JWT" in result.question.question
        # Engineer + EM escalation attempt
        assert call_count == 2

    def test_blocked_engineer_escalates_then_pauses(self) -> None:
        """Engineer reports blocked, escalation cannot resolve -> task pauses."""
        invoker = MagicMock()

        def side_effect(role: str, prompt: str, **kwargs: object) -> dict[str, object]:
            if role == "backend_engineer":
                return _impl_result(
                    status="blocked",
                    question="Cannot access required service",
                )
            # Escalation: nobody can resolve, eventually needs human
            return {"resolved": False, "reasoning": "Cannot determine"}

        invoker.invoke.side_effect = side_effect
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.WAITING_ON_QUESTION
        assert result.question is not None

    def test_escalation_resolves_question_re_executes_engineer(self) -> None:
        """Engineer raises question, escalation resolves it -> engineer re-invoked."""
        invoker = MagicMock()
        engineer_calls = 0
        em_calls = 0

        def side_effect(role: str, prompt: str, **kwargs: object) -> dict[str, object]:
            nonlocal engineer_calls, em_calls
            if role == "backend_engineer":
                engineer_calls += 1
                if engineer_calls == 1:
                    return _impl_result(
                        status="needs_clarification",
                        question="JWT or sessions?",
                    )
                # Second call: engineer proceeds with the answer
                return _impl_result()
            if role == "em_team_a":
                em_calls += 1
                if em_calls == 1:
                    # Escalation: EM resolves the question
                    return {
                        "resolved": True,
                        "answer": "Use JWT",
                        "reasoning": "Matches our auth stack",
                    }
                # Subsequent EM calls are review calls
                return _review_result("approved")
            if role == "frontend_engineer":
                return _review_result("approved")
            return _review_result("approved")

        invoker.invoke.side_effect = side_effect
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.APPROVED
        assert engineer_calls == 2

    def test_escalation_needs_human_task_pauses(self) -> None:
        """Escalation reaches human level -> task pauses with question."""
        invoker = MagicMock()

        def side_effect(role: str, prompt: str, **kwargs: object) -> dict[str, object]:
            if role == "backend_engineer":
                return _impl_result(
                    status="blocked",
                    question="External API is down",
                )
            # All escalation levels fail
            return {"resolved": False, "reasoning": "Cannot help"}

        invoker.invoke.side_effect = side_effect
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.WAITING_ON_QUESTION
        assert result.question is not None
        assert "External API" in result.question.question


# ---------------------------------------------------------------------------
# Feedback injection
# ---------------------------------------------------------------------------


class TestFeedbackInjection:
    def test_human_feedback_included_in_prompt(self) -> None:
        """Operator comment is passed to the engineer."""
        invoker = MagicMock()
        invoker.invoke.side_effect = [
            _impl_result(),
            _review_result("approved"),
            _review_result("approved"),
        ]
        ctx = _make_ctx()
        ctx.feedback = "Use PostgreSQL, not SQLite"

        execute_task_workflow(ctx, invoker)

        first_call_kwargs = invoker.invoke.call_args_list[0].kwargs
        assert "Use PostgreSQL" in first_call_kwargs["prompt"]


# ---------------------------------------------------------------------------
# Invalid agent payload handling
# ---------------------------------------------------------------------------


class TestInvalidAgentPayloads:
    def test_invalid_implementation_result_propagates(self) -> None:
        """Engineer returns malformed payload -- ValidationError propagates."""
        from pydantic import ValidationError

        invoker = MagicMock()
        invoker.invoke.return_value = {"summary": "partial"}  # missing required fields
        ctx = _make_ctx()

        with pytest.raises(ValidationError):
            engineer_execute(ctx, invoker)

    def test_invalid_review_result_propagates(self) -> None:
        """Peer review returns malformed payload -- ValidationError propagates."""
        from pydantic import ValidationError

        invoker = MagicMock()
        invoker.invoke.return_value = {"verdict": "invalid_value", "summary": "ok", "comments": []}
        ctx = _make_ctx()
        impl = ImplementationResult.model_validate(_impl_result())

        with pytest.raises(ValidationError):
            peer_review(ctx, impl, invoker)

    def test_invalid_review_missing_comments_for_rejection(self) -> None:
        """Review verdict needs_revision with empty comments fails validation."""
        from pydantic import ValidationError

        invoker = MagicMock()
        invoker.invoke.return_value = {
            "verdict": "needs_revision",
            "summary": "needs work",
            "comments": [],
        }
        ctx = _make_ctx()
        impl = ImplementationResult.model_validate(_impl_result())

        with pytest.raises(ValidationError, match="requires at least one comment"):
            peer_review(ctx, impl, invoker)


# ---------------------------------------------------------------------------
# Peer needs_revision regression
# ---------------------------------------------------------------------------


class TestPeerNeedsRevisionRegression:
    def test_peer_needs_revision_triggers_revision_loop(self) -> None:
        """Peer returning needs_revision must trigger revision, not fall through to EM."""
        invoker = MagicMock()
        call_order: list[str] = []

        def track_invoke(role: str, prompt: str, **kwargs: object) -> dict[str, object]:
            call_order.append(role)
            if role == "backend_engineer":
                return _impl_result()
            elif role == "frontend_engineer":
                # First peer review: needs_revision; second: approved
                peer_count = sum(1 for c in call_order if c == "frontend_engineer")
                if peer_count == 1:
                    return _review_result("needs_revision")
                return _review_result("approved")
            elif role == "em_team_a":
                return _review_result("approved")
            raise ValueError(f"Unexpected role: {role}")

        invoker.invoke.side_effect = track_invoke
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.APPROVED
        # After the peer needs_revision, engineer must be re-invoked before EM
        first_peer_idx = call_order.index("frontend_engineer")
        assert call_order[first_peer_idx + 1] == "backend_engineer"
        # EM should only be called once (in the second iteration)
        assert call_order.count("em_team_a") == 1
        assert result.revision_count == 1


# ---------------------------------------------------------------------------
# Structured revision feedback
# ---------------------------------------------------------------------------


class TestBuildRevisionFeedback:
    def test_includes_summary_and_comments(self) -> None:
        """Revision feedback should include structured review comments."""
        review = ReviewResult(
            verdict="needs_revision",
            summary="Missing error handling",
            comments=[
                ReviewComment(
                    file="src/api.py",
                    line=42,
                    severity="error",
                    comment="Unchecked null return",
                ),
                ReviewComment(
                    file="src/api.py",
                    line=78,
                    severity="warning",
                    comment="Consider using a context manager",
                ),
            ],
        )
        feedback = _build_revision_feedback(review)
        assert "Review summary: Missing error handling" in feedback
        assert "src/api.py:42 [error] Unchecked null return" in feedback
        assert "src/api.py:78 [warning] Consider using a context manager" in feedback

    def test_no_comments_only_summary(self) -> None:
        """When no comments, revision feedback is just the summary."""
        review = ReviewResult(
            verdict="approved",
            summary="All good",
            comments=[],
        )
        feedback = _build_revision_feedback(review)
        assert feedback == "Review summary: All good"

    def test_structured_feedback_passed_to_engineer(self) -> None:
        """When EM rejects with comments, engineer gets structured feedback."""
        invoker = MagicMock()
        em_calls = 0

        def side_effect(role: str, prompt: str, **kwargs: object) -> dict[str, object]:
            nonlocal em_calls
            if role == "backend_engineer":
                return _impl_result()
            if role == "frontend_engineer":
                return _review_result("approved")
            if role == "em_team_a":
                em_calls += 1
                if em_calls == 1:
                    return _review_result("needs_revision")
                return _review_result("approved")
            raise ValueError(f"Unexpected role: {role}")

        invoker.invoke.side_effect = side_effect
        ctx = _make_ctx()

        result = execute_task_workflow(ctx, invoker)

        assert result.status == TaskStatus.APPROVED
        # The second engineer call should have structured revision feedback
        engineer_calls = [
            c for c in invoker.invoke.call_args_list
            if c.kwargs.get("role") == "backend_engineer"
        ]
        assert len(engineer_calls) >= 2
        second_prompt = engineer_calls[1].kwargs["prompt"]
        assert "Review summary:" in second_prompt
