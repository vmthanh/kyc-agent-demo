"""Deterministic policy engine.

This module is the safety boundary of the agent. It is pure (no I/O, no
model calls, no language) and cheap to unit test exhaustively. `evaluate()`
computes the mandated outcome directly from typed, tool-sourced facts.
`guard()` compares that mandate against whatever the planner (LLM or
fallback) proposed and always keeps the mandate -- the model may explain and
add nuance, but it can never talk its way past a compliance stop, an
identity conflict, or an evidence gap, even if the case note tries to
instruct it to.

Verdicts carry a `reason_key` + `reason_params` pair, not a pre-rendered
sentence: `app/i18n.py` renders the actual English/Vietnamese text at the
presentation boundary. This module has zero knowledge of display language.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .domain import LLMProposal, Outcome

SANCTIONS_THRESHOLD = 0.80


@dataclass(frozen=True)
class PolicyVerdict:
    outcome: Outcome
    action: str | None
    risk_level: str
    reason_key: str
    reason_params: dict[str, Any] = field(default_factory=dict)


def evaluate(facts: dict[str, Any]) -> PolicyVerdict:
    """Recompute the mandated outcome from grounded, typed facts only.

    Branch order matters and is exhaustive: a sanctions hit always wins,
    then identity integrity, then evidence completeness, then the
    no-exception case. Nothing here reads free-text case notes.
    """
    docs = facts["verify_documents"]
    sanctions = facts["screen_sanctions"]
    risk = facts["get_risk_profile"]

    if sanctions["match_score"] >= SANCTIONS_THRESHOLD:
        return PolicyVerdict(
            Outcome.ESCALATE_COMPLIANCE,
            None,
            "CRITICAL",
            "sanctions_hit",
            {"score": sanctions["match_score"], "threshold": SANCTIONS_THRESHOLD},
        )
    if not docs["name_match"] or not docs["liveness_passed"]:
        return PolicyVerdict(Outcome.MANUAL_REVIEW, "open_manual_review", "HIGH", "identity_conflict")
    if docs["missing_fields"]:
        return PolicyVerdict(
            Outcome.REQUEST_EVIDENCE,
            "request_document",
            risk["level"],
            "missing_evidence",
            {"fields": list(docs["missing_fields"])},
        )
    return PolicyVerdict(Outcome.CLEAR, None, risk["level"], "clear")


def tags_for(facts: dict[str, Any]) -> set[str]:
    """Policy-retrieval tags derived from the same branch logic as `evaluate`."""
    docs = facts["verify_documents"]
    sanctions = facts["screen_sanctions"]
    tags = {"kyc", "risk_tier"}
    if sanctions["match_score"] >= SANCTIONS_THRESHOLD:
        tags.add("sanctions")
    elif not docs["name_match"] or not docs["liveness_passed"]:
        tags.add("identity_mismatch")
    elif docs["missing_fields"]:
        tags.add("missing_evidence")
    else:
        tags.add("clear")
    return tags


@dataclass(frozen=True)
class GuardResult:
    verdict: PolicyVerdict
    override_info: dict[str, Any] | None  # None when the model's proposal already matched the mandate


def guard_verdict(verdict: PolicyVerdict, proposal: LLMProposal) -> GuardResult:
    """Reconcile an already-computed mandate with an advisory proposal."""
    if proposal.outcome == verdict.outcome.value and proposal.action == verdict.action:
        return GuardResult(verdict, None)
    return GuardResult(verdict, {
        "model": proposal.model,
        "proposal_outcome": proposal.outcome,
        "proposal_action": proposal.action,
        "final_outcome": verdict.outcome.value,
        "final_action": verdict.action,
        "reason_key": verdict.reason_key,
        "reason_params": verdict.reason_params,
    })


def guard(facts: dict[str, Any], proposal: LLMProposal) -> GuardResult:
    """Verify the planner's proposal against the independently computed mandate."""
    return guard_verdict(evaluate(facts), proposal)
