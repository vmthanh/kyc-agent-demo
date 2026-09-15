from typing import Literal

from .state import WorkflowState


def route_after_evidence_gate(state: WorkflowState) -> Literal["retrieve_policy", "operational_review"]:
    fact_keys = ("customer_facts", "document_facts", "screening_facts", "risk_facts")
    return "retrieve_policy" if all(state.get(key) is not None for key in fact_keys) else "operational_review"


def route_after_policy_precheck(state: WorkflowState) -> Literal["openrouter_reason", "operational_review"]:
    return "openrouter_reason" if state.get("policy_status") == "ok" else "operational_review"


def route_after_planner(state: WorkflowState) -> Literal["reconcile_guard", "safe_failure"]:
    return "reconcile_guard" if state.get("planner_status") == "ok" else "safe_failure"


def route_after_safe_failure(state: WorkflowState) -> Literal["finalize_blocked", "operational_review"]:
    verdict = state.get("policy_verdict") or {}
    return "finalize_blocked" if verdict.get("outcome") == "ESCALATE_COMPLIANCE" else "operational_review"


def route_after_guard(
    state: WorkflowState,
) -> Literal["finalize", "finalize_blocked", "action_review", "operational_review"]:
    outcome = state["decision"]["outcome"]
    if outcome == "CLEAR":
        return "finalize"
    if outcome == "ESCALATE_COMPLIANCE":
        return "finalize_blocked"
    if outcome == "REQUEST_EVIDENCE" and state["cycle_count"] >= state["max_cycles"]:
        return "operational_review"
    return "action_review"


def route_after_review(state: WorkflowState) -> Literal["execute_action", "finalize_rejected"]:
    return "execute_action" if state["review_result"]["approved"] else "finalize_rejected"


def route_after_action(state: WorkflowState) -> Literal["await_documents", "finalize", "operational_review"]:
    result = state.get("action_result") or {}
    if result.get("status") != "executed":
        return "operational_review"
    return "await_documents" if result.get("action") == "request_document" else "finalize"


def route_after_submission(state: WorkflowState) -> Literal["increment_cycle", "await_documents"]:
    return "increment_cycle" if state.get("submission_valid") else "await_documents"
