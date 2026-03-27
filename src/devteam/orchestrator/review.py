"""Route-appropriate review chain enforcement.

Determines which post-PR review gates apply based on work type.
Pre-PR review (peer + EM) is handled by task_workflow.py.
This module handles post-PR shared services review.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

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
        ReviewGate(name="engineer_review", reviewer_role="backend_engineer", required=True),
    ),
}


def get_review_chain(work_type: WorkType, assigned_to: str | None = None) -> ReviewChain:
    """Get the review chain for a given work type.

    For DOCUMENTATION work, the engineer_review gate uses the task's
    assigned_to role instead of the hardcoded default.
    """
    gates = REVIEW_CHAINS.get(work_type, ())
    if work_type == WorkType.DOCUMENTATION and assigned_to:
        gates = tuple(
            ReviewGate(name=g.name, reviewer_role=assigned_to, required=g.required)
            if g.name == "engineer_review"
            else g
            for g in gates
        )
    return ReviewChain(work_type=work_type, gates=gates)


def is_small_fix_with_no_behavior_change(
    work_type: WorkType,
    files_changed: list[str],
) -> bool:
    """Determine if a small fix has no behavior change (skip QA)."""
    if not files_changed:
        return False
    if work_type != WorkType.CODE:
        return False
    # Heuristic: only clearly non-executable documentation files qualify.
    # Config files (.yml, .yaml, .toml, .json) and style files (.css, .scss)
    # can affect runtime behavior and must not be skipped.
    _NON_BEHAVIORAL_EXTENSIONS = frozenset({".md", ".rst", ".txt", ".adoc"})
    return all(any(f.endswith(ext) for ext in _NON_BEHAVIORAL_EXTENSIONS) for f in files_changed)


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
    assigned_to: str | None = None,
) -> PostPRReviewResult:
    """Execute the post-PR review chain for a work type.

    Each gate is executed in sequence. If a required gate fails,
    the chain stops (caller decides whether to trigger revision).
    """
    chain = get_review_chain(work_type, assigned_to=assigned_to)
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
            if not gate.required:
                # Optional gate transport error: treat as failed-but-non-blocking
                failed_gates.append(gate.name)
                continue
            raise RuntimeError(f"Post-PR review gate '{gate.name}' invocation failed: {e}") from e

        try:
            result = ReviewResult.model_validate(raw)
        except ValidationError as e:
            if not gate.required:
                # Optional gate malformed response: treat as failed-but-non-blocking
                failed_gates.append(gate.name)
                continue
            raise RuntimeError(
                f"Post-PR review gate '{gate.name}' returned invalid payload: {e}"
            ) from e
        gate_results[gate.name] = result

        if result.needs_revision:
            failed_gates.append(gate.name)
            if gate.required:
                # Stop the chain on required gate failure
                break

    # all_passed is True if no REQUIRED gates failed
    required_gate_names = {g.name for g in chain.gates if g.required}
    required_failures = [g for g in failed_gates if g in required_gate_names]
    return PostPRReviewResult(
        all_passed=len(required_failures) == 0,
        gate_results=gate_results,
        failed_gates=failed_gates,
        skipped_gates=skipped_gates,
    )
