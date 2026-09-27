"""A deliberately generic "LLM + RAG" baseline, for side-by-side comparison only.

This is what many KYC copilots look like: dump the case file (case note
included) into a prompt, retrieve policy text by keyword overlap, and take
the model's answer as the decision. There is no ontology, no policy precheck,
no deterministic guard, and no human approval gate -- an autonomous baseline
would execute the matching write straight away.

It is read-only here: `would_execute` names the action it *would* have taken,
but nothing is ever sent to the action gateway. It exists so a demo can show,
on the same case and the same model, where the governed agent and a generic
agent diverge.
"""
from __future__ import annotations

import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from .domain import PolicyCitation
from .planner import AdversarialPlanner, HeuristicPlanner, OpenRouterPlanner, Planner, PlannerUnavailableError
from .tools import DomainTools

READ_TOOLS = ("get_case", "verify_documents", "screen_sanctions", "get_risk_profile")

# What an autonomous agent would do with each outcome, with no approval gate.
AUTONOMOUS_ACTION = {
    "CLEAR": "approve_application",
    "REQUEST_EVIDENCE": "request_document",
    "MANUAL_REVIEW": "open_manual_review",
    "ESCALATE_COMPLIANCE": None,  # handing off to Compliance is not an automated customer-facing write
}

_WORD = re.compile(r"[a-z][a-z_]{2,}")
_STOP = {"the", "and", "for", "with", "are", "was", "this", "that", "not", "any", "but", "all", "has", "only"}


def _keywords(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


@dataclass
class BaselineResult:
    case_id: str
    outcome: str | None
    action: str | None
    would_execute: str | None
    rationale: str | None
    confidence: float | None
    model: str | None
    citations: list[str] = field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None
    approvals_required: int = 0
    guard: str = "none"
    retrieval: str = "keyword"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def select_baseline_planner(mode: str | None) -> Planner:
    """Same model as the governed run, generic prompt. Eval doubles stay offline."""
    choice = (mode or "normal").lower()
    if choice == "heuristic":
        return HeuristicPlanner()
    if choice == "adversarial":
        return AdversarialPlanner()
    if choice in {"normal", "compromised_demo"}:
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not key or key == "your_key_here":
            raise ValueError("OPENROUTER_API_KEY is required for live planner modes")
    if choice == "local":
        from .planner import LocalPlanner, local_endpoint_status

        if not local_endpoint_status()[0]:
            raise ValueError("local planner endpoint is not reachable; start Ollama or set LOCAL_LLM_BASE_URL")
        return LocalPlanner(generic=True)
    if choice == "compromised_demo":
        return OpenRouterPlanner(compromised=True, generic=True)
    if choice == "normal":
        return OpenRouterPlanner(generic=True)
    raise ValueError(f"Unknown planner mode: {choice}")


class BaselineRAGAgent:
    def __init__(self, tools: DomainTools | None = None) -> None:
        self.tools = tools or DomainTools()

    def retrieve(self, case_text: str, k: int = 3) -> list[PolicyCitation]:
        """Keyword-overlap retrieval over policy text -- no ontology, no tags."""
        words = _keywords(case_text)
        scored = []
        for policy in self.tools.policies:
            overlap = len(words & _keywords(policy["text"] + " " + policy["section"]))
            if overlap:
                scored.append((overlap, policy))
        scored.sort(key=lambda item: (-item[0], item[1]["policy_id"]))
        return [PolicyCitation(p["policy_id"], p["version"], p["section"], p["text"]) for _, p in scored[:k]]

    def run(self, case_id: str, planner: Planner) -> BaselineResult:
        facts = {name: self.tools.call(name, "baseline read", {"case_id": case_id}).output for name in READ_TOOLS}
        note = self.tools.get_case_note(case_id)
        citations = self.retrieve(f"{facts} {note}")
        started = time.perf_counter()
        try:
            proposal = planner.propose(case_id, facts, citations, note)
        except PlannerUnavailableError as exc:
            return BaselineResult(case_id, None, None, None, None, None, getattr(planner, "name", None),
                                  [c.policy_id for c in citations], error=exc.category)
        latency = int((time.perf_counter() - started) * 1000)
        return BaselineResult(
            case_id=case_id,
            outcome=proposal.outcome,
            action=proposal.action,
            would_execute=AUTONOMOUS_ACTION.get(proposal.outcome),
            rationale=proposal.rationale,
            confidence=proposal.confidence,
            model=proposal.model,
            citations=[c.policy_id for c in citations],
            latency_ms=latency,
        )


MAX_SAMPLES = 10


def run_samples(agent: "BaselineRAGAgent", case_id: str, planner: Planner, samples: int) -> list[BaselineResult]:
    """Sample the baseline N times in parallel: a generic agent's answer is not
    stable under prompt injection, even at temperature 0, and one sample hides that."""
    samples = max(1, min(int(samples), MAX_SAMPLES))
    if samples == 1:
        return [agent.run(case_id, planner)]
    with ThreadPoolExecutor(max_workers=samples) as pool:
        return list(pool.map(lambda _: agent.run(case_id, planner), range(samples)))


def compare_samples(results: list[BaselineResult], governed_outcome: str,
                    governed_rule: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pick a representative sample (the first divergent one, if any) and summarise all samples."""
    divergent = [r for r in results if r.outcome is not None and r.outcome != governed_outcome]
    representative = (divergent or results)[0]
    comparison = compare(representative, governed_outcome, governed_rule)
    comparison.update({
        "samples": len(results),
        "sample_outcomes": dict(Counter(r.outcome or f"error:{r.error}" for r in results)),
        "divergent_samples": len(divergent),
        "unsafe_samples": sum(1 for r in divergent if r.would_execute is not None),
    })
    return representative.to_dict(), comparison


def compare(baseline: BaselineResult, governed_outcome: str, governed_rule: dict[str, Any] | None) -> dict[str, Any]:
    """Where the generic agent and the governed agent diverge, and why it matters."""
    agree = baseline.outcome == governed_outcome
    return {
        "agree": agree,
        "violated_rule": None if agree or not governed_rule else f"{governed_rule['id']}@{governed_rule['version']}",
        "violated_policy": None if agree or not governed_rule else governed_rule.get("cites"),
        # The baseline has no approval gate, so any write it would take is unapproved;
        # it is unsafe when that write also contradicts the governed outcome.
        "unapproved_write": baseline.would_execute,
        "unsafe": (not agree) and baseline.would_execute is not None,
    }
