"""Question escalation workflow -- pause branch, route to supervisor chain.

Questions pause individual task branches while other branches continue.
Escalation: supervisor -> leadership -> human.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from devteam.orchestrator.routing import InvokerProtocol
from devteam.orchestrator.schemas import (
    EscalationLevel,
    QuestionRecord,
    QuestionType,
)


# Escalation routing based on question type.
# Uses the actual QuestionType values from agents.contracts (not the plan's
# original ARCHITECTURE / ROUTING_POLICY / SPEC_AMBIGUITY names).
ESCALATION_PATHS: dict[QuestionType, list[str]] = {
    QuestionType.TECHNICAL: ["em"],  # Usually resolved at EM level
    QuestionType.ARCHITECTURAL: ["em", "chief_architect", "human"],
    QuestionType.PRODUCT: ["em", "ceo", "human"],
    QuestionType.PROCESS: ["em", "ceo", "human"],
    QuestionType.BLOCKED: ["em", "chief_architect", "ceo", "human"],
}


@dataclass(frozen=True)
class EscalationAttempt:
    """Result of attempting to resolve a question at one level."""

    level: str
    resolved: bool
    answer: str | None = None
    reasoning: str | None = None


@dataclass
class EscalationResult:
    """Full result of the escalation workflow."""

    question: QuestionRecord
    resolved: bool
    final_level: EscalationLevel
    attempts: list[EscalationAttempt] = field(default_factory=list)
    needs_human: bool = False
    answer: str | None = None


def get_escalation_path(question_type: QuestionType) -> list[str]:
    """Get the escalation path for a question type."""
    return list(ESCALATION_PATHS.get(question_type, ["em", "human"]))


def build_escalation_prompt(question: QuestionRecord, level: str) -> str:
    """Build prompt for a supervisor to attempt answering a question."""
    return (
        f"## Question Escalated to You\n\n"
        f"**Question type:** {question.question_type.value}\n"
        f"**Question:** {question.question}\n\n"
        f"Can you answer this question within your authority as {level}?\n"
        f"If yes, provide the answer.\n"
        f"If no, explain why this needs to be escalated further.\n\n"
        f"Return a JSON object with:\n"
        f'- "resolved": true/false\n'
        f'- "answer": your answer (if resolved)\n'
        f'- "reasoning": why you can or cannot answer\n'
    )


def attempt_resolution(
    question: QuestionRecord,
    level: str,
    invoker: InvokerProtocol,
) -> EscalationAttempt:
    """Attempt to resolve a question at a given escalation level."""
    prompt = build_escalation_prompt(question, level)
    try:
        raw = invoker.invoke(
            role=level,
            prompt=prompt,
        )
    except Exception as e:
        raise RuntimeError(f"Escalation attempt to '{level}' failed: {e}") from e

    # Parse the response (agent returns dict with resolved, answer, reasoning)
    resolved = raw.get("resolved", False)
    return EscalationAttempt(
        level=level,
        resolved=bool(resolved),
        answer=raw.get("answer"),
        reasoning=raw.get("reasoning"),
    )


def escalate_question(
    question: QuestionRecord,
    invoker: InvokerProtocol,
    em_role: str = "em_team_a",
) -> EscalationResult:
    """Run the escalation workflow for a question.

    Walks up the escalation chain for the question type.
    If resolved at any level, returns immediately.
    If all agent levels fail, marks as needing human input.
    """
    path = get_escalation_path(question.question_type)
    attempts: list[EscalationAttempt] = []

    # Replace generic "em" with the specific EM role
    path = [em_role if level == "em" else level for level in path]

    for level in path:
        if level == "human":
            # Reached the end of the agent chain
            return EscalationResult(
                question=question,
                resolved=False,
                final_level=EscalationLevel.HUMAN,
                attempts=attempts,
                needs_human=True,
            )

        attempt = attempt_resolution(question, level, invoker)
        attempts.append(attempt)

        if attempt.resolved:
            # Determine escalation level based on where it was resolved
            if level == em_role:
                final_level = EscalationLevel.SUPERVISOR
            else:
                final_level = EscalationLevel.LEADERSHIP

            return EscalationResult(
                question=question,
                resolved=True,
                final_level=final_level,
                attempts=attempts,
                answer=attempt.answer,
            )

    # Should not reach here if path includes "human", but handle gracefully
    return EscalationResult(
        question=question,
        resolved=False,
        final_level=EscalationLevel.HUMAN,
        attempts=attempts,
        needs_human=True,
    )


def resolve_with_human_answer(
    question: QuestionRecord,
    answer: str,
) -> EscalationResult:
    """Resolve a question with a human-provided answer.

    Called when the operator uses ``devteam answer <question-id> "..."``.
    Returns an EscalationResult with the human answer.
    """
    return EscalationResult(
        question=question,
        resolved=True,
        final_level=EscalationLevel.HUMAN,
        answer=answer,
    )
