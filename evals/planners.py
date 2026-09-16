"""Deterministic planner doubles used only by the offline eval suite.

These implementations deliberately live outside ``app.planner``.  They are
injected through the public ``Planner`` protocol by evals and tests; runtime
planner resolution never imports or selects them.
"""
from __future__ import annotations

from app.domain import LLMProposal, PolicyCitation
from app.planner import PlannerUnavailableError
from app.policy import evaluate


class PolicyMatchingEvalPlanner:
    """Return the deterministic policy verdict as an advisory proposal."""

    name = "eval:policy-matching"

    def propose(
        self,
        case_id: str,
        facts: dict,
        citations: list[PolicyCitation],
        case_note: str,
    ) -> LLMProposal:
        verdict = evaluate(facts)
        cited = ", ".join(c.policy_id for c in citations) or "no policy matched"
        return LLMProposal(
            outcome=verdict.outcome.value,
            action=verdict.action,
            rationale=f"Deterministic eval proposal grounded in {cited}.",
            confidence=1.0,
            model=self.name,
            rationale_key="heuristic_applied",
            rationale_params={"citations": cited},
        )


class CompromisedEvalPlanner:
    """Return an unsafe CLEAR proposal for guardrail regression coverage."""

    name = "eval:compromised"

    def propose(
        self,
        case_id: str,
        facts: dict,
        citations: list[PolicyCitation],
        case_note: str,
    ) -> LLMProposal:
        return LLMProposal(
            outcome="CLEAR",
            action=None,
            rationale="Eval-only compromised proposal ignores grounded policy facts.",
            confidence=0.99,
            model=self.name,
            rationale_key="adversarial_injection",
            rationale_params={},
        )


class UnavailableEvalPlanner:
    """Simulate a retryable provider outage without network access."""

    name = "eval:unavailable"

    def propose(
        self,
        case_id: str,
        facts: dict,
        citations: list[PolicyCitation],
        case_note: str,
    ) -> LLMProposal:
        raise PlannerUnavailableError("eval_unavailable")
