"""Tests for route-appropriate review chain enforcement."""

import pytest
from unittest.mock import MagicMock

from devteam.orchestrator.review import (
    ReviewChain,
    ReviewGate,
    execute_post_pr_review,
    get_review_chain,
    is_small_fix_with_no_behavior_change,
)
from devteam.orchestrator.schemas import (
    WorkType,
)


def _review(verdict: str = "approved") -> dict[str, object]:
    base: dict[str, object] = {
        "verdict": verdict,
        "summary": "ok" if verdict == "approved" else "issues found",
    }
    if verdict in ("needs_revision", "approved_with_comments", "blocked"):
        base["comments"] = [
            {"file": "src/api.py", "line": 10, "severity": "warning", "comment": "Issue found"}
        ]
    else:
        base["comments"] = []
    return base


# ---------------------------------------------------------------------------
# ReviewGate / ReviewChain (frozen dataclasses)
# ---------------------------------------------------------------------------


class TestReviewGateImmutability:
    def test_gate_is_frozen(self) -> None:
        gate = ReviewGate(name="qa", reviewer_role="qa_engineer")
        with pytest.raises(AttributeError):
            gate.name = "other"  # type: ignore[misc]

    def test_chain_is_frozen(self) -> None:
        chain = ReviewChain(work_type=WorkType.CODE)
        with pytest.raises(AttributeError):
            chain.work_type = WorkType.RESEARCH  # type: ignore[misc]


# ---------------------------------------------------------------------------
# get_review_chain
# ---------------------------------------------------------------------------


class TestGetReviewChain:
    def test_code_gets_full_chain(self) -> None:
        chain = get_review_chain(WorkType.CODE)
        names = chain.gate_names
        assert "qa_review" in names
        assert "security_review" in names
        assert "tech_writer_review" in names

    def test_research_gets_ca_only(self) -> None:
        chain = get_review_chain(WorkType.RESEARCH)
        names = chain.gate_names
        assert names == ["ca_review"]

    def test_planning_gets_ca_only(self) -> None:
        chain = get_review_chain(WorkType.PLANNING)
        assert chain.gate_names == ["ca_review"]

    def test_architecture_gets_ceo(self) -> None:
        chain = get_review_chain(WorkType.ARCHITECTURE)
        assert chain.gate_names == ["ceo_review"]

    def test_documentation_gets_engineer(self) -> None:
        chain = get_review_chain(WorkType.DOCUMENTATION)
        assert chain.gate_names == ["engineer_review"]

    def test_documentation_gate_is_required(self) -> None:
        chain = get_review_chain(WorkType.DOCUMENTATION)
        assert chain.gates[0].required

    def test_code_gates_are_required(self) -> None:
        chain = get_review_chain(WorkType.CODE)
        assert all(g.required for g in chain.gates)

    def test_documentation_gate_uses_assigned_to(self) -> None:
        chain = get_review_chain(WorkType.DOCUMENTATION, assigned_to="frontend_engineer")
        assert chain.gates[0].reviewer_role == "frontend_engineer"

    def test_documentation_gate_default_reviewer(self) -> None:
        chain = get_review_chain(WorkType.DOCUMENTATION)
        assert chain.gates[0].reviewer_role == "backend_engineer"


# ---------------------------------------------------------------------------
# Small fix detection
# ---------------------------------------------------------------------------


class TestSmallFixDetection:
    def test_docs_only_is_no_behavior_change(self) -> None:
        assert is_small_fix_with_no_behavior_change(WorkType.CODE, ["README.md", "docs/guide.md"])

    def test_python_files_are_behavior_change(self) -> None:
        assert not is_small_fix_with_no_behavior_change(WorkType.CODE, ["src/api.py"])

    def test_mixed_files_are_behavior_change(self) -> None:
        assert not is_small_fix_with_no_behavior_change(WorkType.CODE, ["README.md", "src/api.py"])

    def test_non_code_work_type_always_false(self) -> None:
        assert not is_small_fix_with_no_behavior_change(WorkType.RESEARCH, ["README.md"])

    def test_config_files_are_behavior_change(self) -> None:
        """Config files (.toml, .json, .yml) can affect runtime behavior."""
        assert not is_small_fix_with_no_behavior_change(
            WorkType.CODE, ["config.toml", "settings.json", "docker-compose.yml"]
        )

    def test_rst_files_are_no_behavior_change(self) -> None:
        assert is_small_fix_with_no_behavior_change(
            WorkType.CODE, ["docs/index.rst", "CHANGELOG.txt"]
        )

    def test_adoc_files_are_no_behavior_change(self) -> None:
        assert is_small_fix_with_no_behavior_change(WorkType.CODE, ["docs/guide.adoc"])

    def test_empty_files_list(self) -> None:
        # Empty list should be treated as "no info" and return False
        assert not is_small_fix_with_no_behavior_change(WorkType.CODE, [])


# ---------------------------------------------------------------------------
# execute_post_pr_review
# ---------------------------------------------------------------------------


class TestExecutePostPRReview:
    def test_code_all_pass(self) -> None:
        invoker = MagicMock()
        invoker.invoke.return_value = _review("approved")

        result = execute_post_pr_review(WorkType.CODE, "PR context", invoker)

        assert result.all_passed
        assert len(result.gate_results) == 3
        assert "qa_review" in result.gate_results
        assert "security_review" in result.gate_results
        assert "tech_writer_review" in result.gate_results

    def test_research_only_ca_review(self) -> None:
        invoker = MagicMock()
        invoker.invoke.return_value = _review("approved")

        result = execute_post_pr_review(WorkType.RESEARCH, "Research output", invoker)

        assert result.all_passed
        assert len(result.gate_results) == 1
        assert "ca_review" in result.gate_results
        # Verify only chief_architect was invoked
        invoker.invoke.assert_called_once()
        call_kwargs = invoker.invoke.call_args.kwargs
        assert call_kwargs["role"] == "chief_architect"

    def test_security_failure_stops_chain(self) -> None:
        """If security fails, tech writer review should not run."""
        invoker = MagicMock()

        def side_effect(role: str, prompt: str, **kwargs: object) -> dict[str, object]:
            if role == "security_engineer":
                return _review("needs_revision")
            return _review("approved")

        invoker.invoke.side_effect = side_effect

        result = execute_post_pr_review(WorkType.CODE, "PR context", invoker)

        assert not result.all_passed
        assert "security_review" in result.failed_gates
        # Tech writer should not have been called
        assert "tech_writer_review" not in result.gate_results

    def test_small_fix_skips_qa(self) -> None:
        """Small fix with no behavior change should skip QA."""
        invoker = MagicMock()
        invoker.invoke.return_value = _review("approved")

        result = execute_post_pr_review(
            WorkType.CODE,
            "PR context",
            invoker,
            files_changed=["README.md", "docs/guide.txt"],
        )

        assert result.all_passed
        assert "qa_review" in result.skipped_gates
        assert "qa_review" not in result.gate_results
        # Security and tech writer should still run
        assert "security_review" in result.gate_results
        assert "tech_writer_review" in result.gate_results

    def test_architecture_uses_ceo(self) -> None:
        invoker = MagicMock()
        invoker.invoke.return_value = _review("approved")

        result = execute_post_pr_review(WorkType.ARCHITECTURE, "ADR content", invoker)

        assert result.all_passed
        assert "ceo_review" in result.gate_results

    def test_documentation_required_gate_failure_blocks(self) -> None:
        """Documentation gate is required -- failure blocks the chain."""
        invoker = MagicMock()
        invoker.invoke.return_value = _review("needs_revision")

        result = execute_post_pr_review(WorkType.DOCUMENTATION, "Docs PR", invoker)

        assert not result.all_passed
        assert "engineer_review" in result.failed_gates

    def test_invoker_failure_wrapped(self) -> None:
        invoker = MagicMock()
        invoker.invoke.side_effect = ConnectionError("timeout")

        with pytest.raises(RuntimeError, match="Post-PR review gate"):
            execute_post_pr_review(WorkType.CODE, "PR context", invoker)

    def test_no_files_changed_runs_all_gates(self) -> None:
        """When files_changed is None, all gates run including QA."""
        invoker = MagicMock()
        invoker.invoke.return_value = _review("approved")

        result = execute_post_pr_review(WorkType.CODE, "PR context", invoker)

        assert "qa_review" in result.gate_results
        assert len(result.skipped_gates) == 0

    def test_malformed_review_missing_verdict(self) -> None:
        """Invoker returns dict without 'verdict' -- RuntimeError wrapping ValidationError."""
        invoker = MagicMock()
        invoker.invoke.return_value = {"summary": "ok"}  # missing verdict

        with pytest.raises(RuntimeError, match="returned invalid payload"):
            execute_post_pr_review(WorkType.CODE, "PR context", invoker)

    def test_malformed_review_invalid_verdict(self) -> None:
        """Invoker returns dict with invalid verdict value -- RuntimeError wrapping ValidationError."""
        invoker = MagicMock()
        invoker.invoke.return_value = {"verdict": "yolo", "summary": "ok", "comments": []}

        with pytest.raises(RuntimeError, match="returned invalid payload"):
            execute_post_pr_review(WorkType.CODE, "PR context", invoker)

    def test_malformed_review_needs_revision_no_comments(self) -> None:
        """needs_revision verdict without comments should fail validation."""
        invoker = MagicMock()
        invoker.invoke.return_value = {
            "verdict": "needs_revision",
            "summary": "bad code",
            "comments": [],
        }

        with pytest.raises(RuntimeError, match="returned invalid payload"):
            execute_post_pr_review(WorkType.CODE, "PR context", invoker)

    def test_all_passed_ignores_optional_gate_failures(self) -> None:
        """all_passed should be True if only optional gates failed."""
        from devteam.orchestrator.review import REVIEW_CHAINS, ReviewGate

        # Temporarily patch CODE to have an optional gate
        original = REVIEW_CHAINS[WorkType.CODE]
        REVIEW_CHAINS[WorkType.CODE] = (
            ReviewGate(name="qa_review", reviewer_role="qa_engineer", required=True),
            ReviewGate(name="optional_check", reviewer_role="optional_reviewer", required=False),
        )
        try:
            invoker = MagicMock()

            def side_effect(role: str, prompt: str, **kwargs: object) -> dict[str, object]:
                if role == "optional_reviewer":
                    return _review("needs_revision")
                return _review("approved")

            invoker.invoke.side_effect = side_effect

            result = execute_post_pr_review(WorkType.CODE, "PR context", invoker)

            # Optional gate failed but all_passed should still be True
            assert result.all_passed
            assert "optional_check" in result.failed_gates
        finally:
            REVIEW_CHAINS[WorkType.CODE] = original

    def test_documentation_uses_assigned_to(self) -> None:
        """Documentation review should use the task's assigned_to role."""
        invoker = MagicMock()
        invoker.invoke.return_value = _review("approved")

        result = execute_post_pr_review(
            WorkType.DOCUMENTATION,
            "Docs PR",
            invoker,
            assigned_to="frontend_engineer",
        )

        call_kwargs = invoker.invoke.call_args.kwargs
        assert call_kwargs["role"] == "frontend_engineer"
        assert result.all_passed
