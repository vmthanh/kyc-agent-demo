from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    CLEAR = "CLEAR"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    ESCALATE_COMPLIANCE = "ESCALATE_COMPLIANCE"


@dataclass(frozen=True)
class PolicyCitation:
    policy_id: str
    version: str
    section: str
    excerpt: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    purpose: str
    input: dict[str, Any]
    output: dict[str, Any]
    duration_ms: int
    status: str = "ok"


@dataclass(frozen=True)
class TraceEvent:
    step: str
    title: str
    detail: str
    status: str = "complete"


@dataclass(frozen=True)
class LLMProposal:
    """What the planner (LLM or fallback) recommends. Advisory only -- the
    deterministic guardrail in `policy.py` always has the final word.

    `rationale` is always English. `rationale_key` + `rationale_params` are
    set only by template-based planners (heuristic, adversarial) so their
    rationale can be rendered in another language offline; a real LLM's
    free-form `rationale` has no key and needs a live translation call.
    """

    outcome: str
    action: str | None
    rationale: str
    confidence: float
    model: str
    rationale_key: str | None = None
    rationale_params: dict[str, Any] | None = None


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    reason: str
    payload: dict[str, Any]
    approval_key: str
    documents_label: str | None = None


@dataclass
class AgentDecision:
    case_id: str
    decision_id: str
    outcome: Outcome
    outcome_label: str
    summary: str
    proposal_confidence: float
    risk_level: str
    risk_label: str
    facts: list[str]
    citations: list[PolicyCitation]
    tool_calls: list[ToolCall]
    trace: list[TraceEvent]
    model: str
    llm_rationale: str
    lang: str = "en"
    rationale_translated: bool = True
    guardrail_override: str | None = None
    approval: ApprovalRequest | None = None
    executed_action: dict[str, Any] | None = None
    ontology_path: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["outcome"] = self.outcome.value
        return value

