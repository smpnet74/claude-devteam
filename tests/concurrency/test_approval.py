"""Tests for configurable approval gates."""

import pytest
from devteam.concurrency.approval import (
    ApprovalPolicy,
    ApprovalGates,
    ApprovalDecision,
    load_approval_gates,
    check_approval,
    DEFAULT_GATES,
)


class TestApprovalPolicy:
    def test_auto_policy(self):
        assert ApprovalPolicy.AUTO.is_auto() is True
        assert ApprovalPolicy.AUTO.is_manual() is False
        assert ApprovalPolicy.AUTO.is_never() is False

    def test_manual_policy(self):
        assert ApprovalPolicy.MANUAL.is_manual() is True
        assert ApprovalPolicy.MANUAL.is_auto() is False

    def test_never_policy(self):
        assert ApprovalPolicy.NEVER.is_never() is True
        assert ApprovalPolicy.NEVER.is_auto() is False

    def test_from_string(self):
        assert ApprovalPolicy.from_string("auto") == ApprovalPolicy.AUTO
        assert ApprovalPolicy.from_string("manual") == ApprovalPolicy.MANUAL
        assert ApprovalPolicy.from_string("never") == ApprovalPolicy.NEVER

    def test_from_string_invalid(self):
        with pytest.raises(ValueError, match="Invalid approval policy"):
            ApprovalPolicy.from_string("sometimes")


class TestApprovalGates:
    def test_default_gates_match_spec(self):
        """Verify defaults match config.toml spec."""
        gates = DEFAULT_GATES
        assert gates.commit == ApprovalPolicy.AUTO
        assert gates.push == ApprovalPolicy.AUTO
        assert gates.open_pr == ApprovalPolicy.AUTO
        assert gates.merge == ApprovalPolicy.AUTO
        assert gates.cleanup == ApprovalPolicy.AUTO
        assert gates.push_to_main == ApprovalPolicy.NEVER

    def test_load_from_config_dict(self):
        config = {
            "approval": {
                "commit": "auto",
                "push": "auto",
                "open_pr": "auto",
                "merge": "manual",
                "cleanup": "auto",
                "push_to_main": "never",
            }
        }
        gates = load_approval_gates(config)
        assert gates.merge == ApprovalPolicy.MANUAL
        assert gates.commit == ApprovalPolicy.AUTO

    def test_load_partial_config_uses_defaults(self):
        config = {"approval": {"merge": "manual"}}
        gates = load_approval_gates(config)
        assert gates.merge == ApprovalPolicy.MANUAL
        assert gates.commit == ApprovalPolicy.AUTO  # default
        assert gates.push_to_main == ApprovalPolicy.NEVER  # always never

    def test_push_to_main_forced_to_never(self):
        """push_to_main is ALWAYS never regardless of config."""
        config = {"approval": {"push_to_main": "auto"}}
        gates = load_approval_gates(config)
        assert gates.push_to_main == ApprovalPolicy.NEVER


class TestCheckApproval:
    def test_auto_action_approved_immediately(self):
        gates = DEFAULT_GATES
        decision = check_approval(gates, "commit")
        assert decision.approved is True
        assert decision.needs_human is False

    def test_manual_action_needs_human(self):
        gates = ApprovalGates(
            commit=ApprovalPolicy.AUTO,
            push=ApprovalPolicy.AUTO,
            open_pr=ApprovalPolicy.AUTO,
            merge=ApprovalPolicy.MANUAL,
            cleanup=ApprovalPolicy.AUTO,
            push_to_main=ApprovalPolicy.NEVER,
        )
        decision = check_approval(gates, "merge")
        assert decision.approved is False
        assert decision.needs_human is True

    def test_never_action_blocked(self):
        gates = DEFAULT_GATES
        decision = check_approval(gates, "push_to_main")
        assert decision.approved is False
        assert decision.needs_human is False
        assert decision.blocked is True

    def test_unknown_action_raises(self):
        gates = DEFAULT_GATES
        with pytest.raises(ValueError, match="Unknown action"):
            check_approval(gates, "deploy")

    def test_check_all_valid_actions(self):
        gates = DEFAULT_GATES
        valid_actions = ["commit", "push", "open_pr", "merge", "cleanup", "push_to_main"]
        for action in valid_actions:
            decision = check_approval(gates, action)
            assert isinstance(decision, ApprovalDecision)
