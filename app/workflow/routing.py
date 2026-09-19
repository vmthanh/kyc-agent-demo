from typing import Literal

from .state import WorkflowState


def route_after_evidence_gate(state: WorkflowState) -> Literal["sufficient", "insufficient"]:
    return "sufficient" if state.get("evidence_ok") else "insufficient"


def route_after_policy_precheck(state: WorkflowState) -> Literal["proceed", "bail"]:
    return "proceed" if state.get("policy_status") == "ok" else "bail"


def route_after_guard(state: WorkflowState) -> Literal["clear", "blocked", "escalate", "needs_action"]:
    decision = state["decision"]
    outcome = decision["outcome"]
    if outcome == "CLEAR":
        return "clear"
    if outcome == "ESCALATE_COMPLIANCE":
        return "blocked"
    if state.get("planner_status") == "failed":
        return "escalate"
    if outcome == "REQUEST_EVIDENCE" and state.get("cycle_count", 1) >= state.get("max_cycles", 2):
        return "escalate"
    return "needs_action"


def route_after_review(state: WorkflowState) -> Literal["approved", "rejected"]:
    return "approved" if state["review_result"]["approved"] else "rejected"


def route_after_action(state: WorkflowState) -> Literal["docs_requested", "done", "escalate"]:
    result = state.get("action_result") or {}
    if result.get("status") != "executed":
        return "escalate"
    return "docs_requested" if result.get("action") == "request_document" else "done"


def route_after_submission(state: WorkflowState) -> Literal["retry", "done", "exhausted"]:
    if state.get("submission_valid"):
        return "done"
    return "exhausted" if state.get("submission_exhausted") else "retry"
