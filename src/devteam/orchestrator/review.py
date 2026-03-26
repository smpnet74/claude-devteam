"""Route-appropriate review chain enforcement.

Determines which post-PR review gates apply based on work type.
Pre-PR review (peer + EM) is handled by task_workflow.py.
This module handles post-PR shared services review.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from devteam.orchestrator.routing import InvokerProtocol
from devteam.orchestrator.schemas import (
    ReviewResult,
    WorkType,
)


@dataclass(frozen=True)
class ReviewGate:
    """A single review gate with its reviewer role."""

    name: str
    reviewer_role: str
    required: bool = True


@dataclass(frozen=True)
class ReviewChain:
    """The full review chain for a work type."""

    work_type: WorkType
    gates: tuple[ReviewGate, ...] = ()

    @property
    def gate_names(self) -> list[str]:
        return [g.name for g in self.gates]


# Review chain definitions per work type
REVIEW_CHAINS: dict[WorkType, tuple[ReviewGate, ...]] = {
    WorkType.CODE: (
        ReviewGate(name="qa_review", reviewer_role="qa_engineer"),
        ReviewGate(name="security_review", reviewer_role="security_engineer"),
        ReviewGate(name="tech_writer_review", reviewer_role="tech_writer"),
    ),
    WorkType.RESEARCH: (ReviewGate(name="ca_review", reviewer_role="chief_architect"),),
    WorkType.PLANNING: (ReviewGate(name="ca_review", reviewer_role="chief_architect"),),
    WorkType.ARCHITECTURE: (ReviewGate(name="ceo_review", reviewer_role="ceo"),),
    WorkType.DOCUMENTATION: (
        ReviewGate(name="engineer_review", reviewer_role="backend_engineer", required=False),
    ),
}


def get_review_chain(work_type: WorkType) -> ReviewChain:
    """Get the review chain for a given work type."""
    gates = REVIEW_CHAINS.get(work_type, ())
    return ReviewChain(work_type=work_type, gates=gates)


def is_small_fix_with_no_behavior_change(
    work_type: WorkType,
    files_changed: list[str],
) -> bool:
    """Determine if a small fix has no behavior change (skip QA)."""
    if work_type != WorkType.CODE:
        return False
    # Heuristic: if only docs/config/style files changed, no behavior change
    non_behavioral_patterns = (
        ".md",
        ".txt",
        ".yml",
        ".yaml",
        ".toml",
        ".json",
        ".css",
        ".scss",
        ".prettierrc",
        ".eslintrc",
    )
    return all(any(f.endswith(p) for p in non_behavioral_patterns) for f in files_changed)


@dataclass
class PostPRReviewResult:
    """Result of running the post-PR review chain."""

    all_passed: bool
    gate_results: dict[str, ReviewResult] = field(default_factory=dict)
    failed_gates: list[str] = field(default_factory=list)
    skipped_gates: list[str] = field(default_factory=list)


def execute_post_pr_review(
    work_type: WorkType,
    pr_context: str,
    invoker: InvokerProtocol,
    files_changed: list[str] | None = None,
    skip_qa_for_no_behavior_change: bool = True,
) -> PostPRReviewResult:
    """Execute the post-PR review chain for a work type.

    Each gate is executed in sequence. If a required gate fails,
    the chain stops (caller decides whether to trigger revision).
    """
    chain = get_review_chain(work_type)
    gate_results: dict[str, ReviewResult] = {}
    failed_gates: list[str] = []
    skipped_gates: list[str] = []

    for gate in chain.gates:
        # Small fix optimization: skip QA if no behavior change
        if (
            skip_qa_for_no_behavior_change
            and gate.name == "qa_review"
            and files_changed
            and is_small_fix_with_no_behavior_change(work_type, files_changed)
        ):
            skipped_gates.append(gate.name)
            continue

        try:
            raw = invoker.invoke(
                role=gate.reviewer_role,
                prompt=(
                    f"## {gate.name.replace('_', ' ').title()}\n\n"
                    f"{pr_context}\n\n"
                    "Review and provide your verdict.\n"
                ),
                json_schema=ReviewResult.model_json_schema(),
            )
        except Exception as e:
            raise RuntimeError(f"Post-PR review gate '{gate.name}' invocation failed: {e}") from e
        result = ReviewResult.model_validate(raw)
        gate_results[gate.name] = result

        if result.needs_revision:
            failed_gates.append(gate.name)
            if gate.required:
                # Stop the chain on required gate failure
                break

    return PostPRReviewResult(
        all_passed=len(failed_gates) == 0,
        gate_results=gate_results,
        failed_gates=failed_gates,
        skipped_gates=skipped_gates,
    )
