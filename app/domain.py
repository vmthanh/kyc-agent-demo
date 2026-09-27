from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    CLEAR = "CLEAR"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    ESCALATE_COMPLIANCE = "ESCALATE_COMPLIANCE"


class WorkflowStatus(str, Enum):
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    AWAITING_DOCUMENTS = "AWAITING_DOCUMENTS"
    AWAITING_OPERATIONS = "AWAITING_OPERATIONS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"


class PendingTaskKind(str, Enum):
    ACTION_APPROVAL = "action_approval"
    DOCUMENT_SUBMISSION = "document_submission"
    OPERATIONAL_REVIEW = "operational_review"


@dataclass(frozen=True)
class PendingTask:
    kind: PendingTaskKind
    interrupt_key: str
    title: str
    message: str
    payload: dict[str, Any]
    allowed_responses: list[str]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["kind"] = self.kind.value
        return value


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
    cycle: int = 1
    duration_ms: int = 0
    attempt: int = 1
    route: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    phase: str | None = None  # SEE | THINK | ACT | REFLECT
    gov_stage: str | None = None  # PROPOSE | VERIFY | COMMIT for governed-write steps


@dataclass(frozen=True)
class LLMProposal:
    """What the planner (live OpenRouter or eval double) recommends. Advisory only -- the
    deterministic guardrail in `policy.py` always has the final word.

    `rationale` is always English. `rationale_key` + `rationale_params` are
    set only by deterministic eval doubles so their
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
    usage: dict[str, Any] = field(default_factory=dict)


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
    proposal_confidence: float | None
    risk_level: str
    risk_label: str
    facts: list[str]
    citations: list[PolicyCitation]
    tool_calls: list[ToolCall]
    trace: list[TraceEvent]
    model: str | None
    llm_rationale: str | None
    lang: str = "en"
    rationale_translated: bool = True
    guardrail_override: str | None = None
    approval: ApprovalRequest | None = None
    executed_action: dict[str, Any] | None = None
    review_result: dict[str, Any] | None = None
    ontology_path: list[str] = field(default_factory=list)
    workflow_status: WorkflowStatus = WorkflowStatus.COMPLETED
    current_node: str = "finalize"
    cycle_count: int = 1
    max_cycles: int = 2
    pending_task: PendingTask | None = None
    planner_attempts: int = 0
    planner_usage: dict[str, Any] = field(default_factory=dict)
    planner_mode: str = "normal"
    governance: dict[str, int] = field(default_factory=dict)  # reads/writes/proposals/overrides/approvals/rejections
    rule: dict[str, Any] | None = None  # {id, version, cites, source, description} of the deciding ontology rule

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["outcome"] = self.outcome.value
        value["workflow_status"] = getattr(self.workflow_status, "value", self.workflow_status)
        value["pending_task"] = self.pending_task.to_dict() if self.pending_task else None
        return value
