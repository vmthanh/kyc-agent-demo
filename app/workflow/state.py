from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict


PlannerMode = Literal["normal", "compromised_demo"]


class WorkflowState(TypedDict, total=False):
    case_id: str
    run_id: str
    lang: str
    planner_mode: PlannerMode
    workflow_status: str
    current_node: str
    cycle_count: int
    max_cycles: int
    submitted_documents: list[dict[str, Any]]
    submission_valid: bool
    customer_facts: dict[str, Any] | None
    document_facts: dict[str, Any] | None
    screening_facts: dict[str, Any] | None
    risk_facts: dict[str, Any] | None
    facts: dict[str, Any]
    tool_calls: Annotated[list[dict[str, Any]], operator.add]
    tool_errors: Annotated[list[dict[str, Any]], operator.add]
    trace: Annotated[list[dict[str, Any]], operator.add]
    citations: list[dict[str, Any]]
    policy_verdict: dict[str, Any] | None
    policy_status: Literal["pending", "ok", "failed"]
    proposal: dict[str, Any] | None
    planner_status: Literal["pending", "ok", "failed"]
    planner_attempts: int
    planner_error: dict[str, Any] | None
    decision: dict[str, Any] | None
    guardrail_override: dict[str, Any] | None
    action_payload: dict[str, Any] | None
    review_result: dict[str, Any] | None
    action_result: dict[str, Any] | None
    document_submission: dict[str, Any] | None
    operational_reason: str | None


def initial_state(
    case_id: str,
    run_id: str,
    lang: str,
    planner_mode: PlannerMode,
    max_cycles: int = 2,
) -> WorkflowState:
    if max_cycles < 1:
        raise ValueError("max_cycles must be at least 1")
    return {
        "case_id": case_id,
        "run_id": run_id,
        "lang": lang,
        "planner_mode": planner_mode,
        "workflow_status": "RUNNING",
        "current_node": "intake",
        "cycle_count": 1,
        "max_cycles": max_cycles,
        "submitted_documents": [],
        "tool_calls": [],
        "tool_errors": [],
        "trace": [],
        "citations": [],
        "policy_status": "pending",
        "planner_status": "pending",
        "planner_attempts": 0,
    }
