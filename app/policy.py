"""Deterministic policy engine.

This module is the safety boundary of the agent. It holds no business rules
of its own: `evaluate()` asks the executable Cognitive Ontology
(`app/ontology.py` over `data/ontology.json`) for the first matching,
versioned rule and returns it as a typed `PolicyVerdict` that names the rule
(`rule_id@rule_version`) and the policy it cites. It is pure (no network, no
model calls, no language) and cheap to unit test exhaustively.
`guard()` compares that mandate against the live planner or an eval double
and always keeps the mandate -- the model may explain and
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
from .ontology import Ontology, load_ontology


def _sanctions_threshold(ontology: Ontology) -> float:
    """Read the hard-stop screening threshold from the ontology (display/back-compat only)."""
    for rule in ontology.rules:
        if rule.hard_stop and "threshold" in rule.params:
            return float(rule.params["threshold"])
    raise LookupError("ontology defines no hard-stop screening threshold")


# Kept for backward-compatible imports; the ontology file is the source of truth.
SANCTIONS_THRESHOLD = _sanctions_threshold(load_ontology())


@dataclass(frozen=True)
class PolicyVerdict:
    outcome: Outcome
    action: str | None
    risk_level: str
    reason_key: str
    reason_params: dict[str, Any] = field(default_factory=dict)
    rule_id: str | None = None
    rule_version: str | None = None
    cites: str | None = None


def evaluate(facts: dict[str, Any], ontology: Ontology | None = None) -> PolicyVerdict:
    """Recompute the mandated outcome from grounded, typed facts only.

    Rule order (hard stops first, fallback last) and the closed rule language
    are enforced when the ontology loads. Nothing here reads free-text case
    notes: the loader rejects any rule that references `case_note`.
    """
    match = (ontology or load_ontology()).evaluate(facts)
    return PolicyVerdict(
        match.outcome, match.action, match.risk_level, match.reason_key, match.reason_params,
        rule_id=match.rule.id, rule_version=match.rule.version, cites=match.rule.cites,
    )


def tags_for(facts: dict[str, Any], ontology: Ontology | None = None) -> set[str]:
    """Policy-retrieval tags: the ontology's base tags plus the matched rule's tags."""
    return (ontology or load_ontology()).tags_for(facts)


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
