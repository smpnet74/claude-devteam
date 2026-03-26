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

    def test_documentation_gate_is_optional(self) -> None:
        chain = get_review_chain(WorkType.DOCUMENTATION)
        assert not chain.gates[0].required

    def test_code_gates_are_required(self) -> None:
        chain = get_review_chain(WorkType.CODE)
        assert all(g.required for g in chain.gates)


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

    def test_config_files_are_no_behavior_change(self) -> None:
        assert is_small_fix_with_no_behavior_change(
            WorkType.CODE, ["config.toml", "settings.json", "docker-compose.yml"]
        )

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
            files_changed=["README.md", "config.toml"],
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

    def test_documentation_optional_gate_failure_still_passes(self) -> None:
        """Documentation gate is optional -- failure does not block."""
        invoker = MagicMock()
        invoker.invoke.return_value = _review("needs_revision")

        result = execute_post_pr_review(WorkType.DOCUMENTATION, "Docs PR", invoker)

        # Gate failed but since it is not required, chain continues
        # (only one gate for documentation, so chain is done)
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
