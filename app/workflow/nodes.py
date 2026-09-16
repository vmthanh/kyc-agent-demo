"""Small, dependency-bound nodes for the policy-first workflow.

The node methods deliberately return *updates*, rather than mutating the
incoming state.  This matters for the four parallel reads: each owns one
fact field and append-only fields contribute only their own reducer value.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from langgraph.types import interrupt

from ..domain import LLMProposal, Outcome, PolicyCitation
from ..planner import Planner
from ..policy import PolicyVerdict, evaluate, guard_verdict, tags_for
from ..tools import DomainTools, action_idempotency_key
from .state import WorkflowState


REQUIRED_POLICY_BY_REASON = {
    "sanctions_hit": "AML-SCREEN-02",
    "identity_conflict": "KYC-IDENTITY-11",
    "missing_evidence": "KYC-EVIDENCE-07",
    "clear": "KYC-CLEAR-01",
}


@dataclass(frozen=True)
class NodeError:
    """Error envelope used by LangGraph retry/error-handler adapters."""

    error: BaseException
    node: str


class WorkflowNodes:
    def __init__(self, tools: DomainTools, planner: Planner) -> None:
        self.tools = tools
        self.planner = planner

    @staticmethod
    def _attempt(runtime: Any = None) -> int:
        info = getattr(runtime, "execution_info", None)
        value = getattr(info, "node_attempt", 1)
        return int(value or 1)

    @staticmethod
    def _event(
        state: WorkflowState,
        step: str,
        title: str,
        detail: str,
        status: str = "complete",
        runtime: Any = None,
    ) -> dict[str, Any]:
        return {
            "step": step,
            "title": title,
            "detail": detail,
            "status": status,
            "cycle": state.get("cycle_count", 1),
            "duration_ms": 0,
            "attempt": WorkflowNodes._attempt(runtime),
            "metadata": {},
        }

    def event(
        self,
        step: str,
        title: str,
        detail: str,
        status: str,
        state: WorkflowState,
        runtime: Any = None,
    ) -> dict[str, Any]:
        """Build a trace event using the public helper order used by handlers."""
        return self._event(state, step, title, detail, status, runtime)

    def intake(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        if not state.get("case_id"):
            raise ValueError("case_id is required")
        if state.get("lang", "en") not in {"en", "vi"}:
            raise ValueError("unsupported language")
        if state.get("planner_mode") not in {"normal", "compromised_demo"}:
            raise ValueError("unsupported planner mode")
        max_cycles = int(state.get("max_cycles", 2))
        if max_cycles < 1:
            raise ValueError("max_cycles must be at least 1")
        return {
            "workflow_status": "RUNNING",
            "current_node": "intake",
            "cycle_count": 1,
            "max_cycles": max_cycles,
            "planner_status": "pending",
            "planner_attempts": 0,
            "trace": [self._event(state, "intake", "Initialize case run", "Validated workflow input", runtime=runtime)],
        }

    def _call_tool(
        self,
        state: WorkflowState,
        node: str,
        field: str,
        tool_name: str,
        purpose: str,
        payload: dict[str, Any],
        runtime: Any = None,
    ) -> dict[str, Any]:
        call = self.tools.call(tool_name, purpose, payload)
        return {
            field: call.output,
            "tool_calls": [asdict(call)],
            "trace": [self._event(state, node, f"Run {tool_name}", f"Loaded authoritative {field}", runtime=runtime)],
        }

    def load_customer(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        return self._call_tool(state, "load_customer", "customer_facts", "get_case", "load customer and application facts", {"case_id": state["case_id"]}, runtime)

    def verify_documents(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        return self._call_tool(
            state, "verify_documents", "document_facts", "verify_documents", "verify identity documents",
            {"case_id": state["case_id"], "submitted_documents": state.get("submitted_documents", [])}, runtime,
        )

    def screen_watchlists(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        return self._call_tool(state, "screen_watchlists", "screening_facts", "screen_sanctions", "screen sanctions and PEP watchlists", {"case_id": state["case_id"]}, runtime)

    def load_risk(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        return self._call_tool(state, "load_risk", "risk_facts", "get_risk_profile", "load risk profile", {"case_id": state["case_id"]}, runtime)

    def evidence_gate(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        fields = {
            "get_case": state.get("customer_facts"),
            "verify_documents": state.get("document_facts"),
            "screen_sanctions": state.get("screening_facts"),
            "get_risk_profile": state.get("risk_facts"),
        }
        if any(value is None for value in fields.values()):
            return {
                "operational_reason": "tool_unavailable",
                "trace": [self._event(state, "evidence_gate", "Evidence incomplete", "Authoritative grounding is unavailable", "degraded", runtime)],
            }
        return {
            "facts": fields,
            "trace": [self._event(state, "evidence_gate", "Evidence complete", "Joined four authoritative fact sources", runtime=runtime)],
        }

    def retrieve_policy(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        citations = self.tools.retrieve_policy(tags_for(state["facts"]))
        return {
            "citations": [asdict(citation) for citation in citations],
            "trace": [self._event(state, "retrieve_policy", "Retrieve versioned policy", f"Selected {len(citations)} policy sections", runtime=runtime)],
        }

    def policy_precheck(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        verdict = evaluate(state["facts"])
        serialized = asdict(verdict)
        serialized["outcome"] = verdict.outcome.value
        required = REQUIRED_POLICY_BY_REASON.get(verdict.reason_key)
        citation_ids = {
            citation.get("policy_id") if isinstance(citation, dict) else citation.policy_id
            for citation in state.get("citations", [])
        }
        covered = required is not None and required in citation_ids
        if not covered:
            return {
                "policy_verdict": serialized,
                "policy_status": "failed",
                "operational_reason": "policy_unavailable",
                "trace": [self._event(state, "policy_precheck", "Policy unavailable", "Required policy coverage is missing", "degraded", runtime)],
            }
        return {
            "policy_verdict": serialized,
            "policy_status": "ok",
            "trace": [self._event(state, "policy_precheck", "Compute mandatory policy outcome", f"Precheck requires {verdict.outcome.value}", runtime=runtime)],
        }

    def openrouter_reason(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        citations = [PolicyCitation(**citation) for citation in state.get("citations", [])]
        proposal = self.planner.propose(
            state["case_id"], state["facts"], citations, self.tools.get_case_note(state["case_id"]),
        )
        attempt = self._attempt(runtime)
        return {
            "proposal": asdict(proposal),
            "planner_status": "ok",
            "planner_attempts": attempt,
            "trace": [self._event(state, "openrouter_reason", f"Planner proposal ({proposal.model})", proposal.outcome, runtime=runtime)],
        }

    def reconcile_guard(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        raw_verdict = dict(state["policy_verdict"])
        raw_verdict["outcome"] = Outcome(raw_verdict["outcome"])
        verdict = PolicyVerdict(**raw_verdict)
        proposal = LLMProposal(**state["proposal"])
        result = guard_verdict(verdict, proposal)
        payload: dict[str, Any] | None = None
        if result.verdict.action:
            payload = {
                "case_id": state["case_id"],
                "action": result.verdict.action,
                "policy_versions": sorted(
                    f"{citation['policy_id']}:{citation['version']}" for citation in state.get("citations", [])
                ),
            }
            if result.verdict.action == "request_document":
                payload["documents"] = sorted(result.verdict.reason_params.get("fields", []))
        decision = {
            "outcome": result.verdict.outcome.value,
            "action": result.verdict.action,
            "risk_level": result.verdict.risk_level,
            "reason_key": result.verdict.reason_key,
            "reason_params": result.verdict.reason_params,
            "action_payload": payload,
            "guardrail_override": result.override_info,
        }
        return {
            "decision": decision,
            "action_payload": payload,
            "guardrail_override": result.override_info,
            "trace": [self._event(state, "reconcile_guard", "Apply deterministic guard", "Model proposal reconciled with policy", "override" if result.override_info else "complete", runtime)],
        }

    def planner_error_handler(self, state: WorkflowState, error: NodeError) -> dict[str, Any]:
        return {
            "planner_status": "failed",
            "planner_attempts": 3,
            "planner_error": {"category": type(error.error).__name__, "node": error.node},
            "trace": [self.event("openrouter_reason", "OpenRouter retries exhausted", "Manual handling required", "degraded", state)],
        }

    def tool_error_handler(self, field: str):
        def handler(state: WorkflowState, error: NodeError) -> dict[str, Any]:
            return {
                field: None,
                "tool_errors": [{"node": error.node, "category": type(error.error).__name__}],
                "trace": [self.event(error.node, "Authoritative tool unavailable", "Manual handling required", "degraded", state)],
            }
        return handler

    def safe_failure(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        verdict = dict(state.get("policy_verdict") or {})
        if verdict.get("outcome") != Outcome.ESCALATE_COMPLIANCE.value:
            verdict.update({"outcome": Outcome.MANUAL_REVIEW.value, "action": None, "reason_key": "ai_unavailable", "reason_params": {}})
            reason = "ai_unavailable"
        else:
            reason = verdict.get("reason_key")
        return {
            "decision": verdict,
            "action_payload": None,
            "operational_reason": reason,
            "trace": [self._event(state, "safe_failure", "Safe failure route", "Deterministic policy remains authoritative", "degraded", runtime)],
        }

    def action_review(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        """Pause before a write, presenting the exact bounded payload to a reviewer."""
        payload = (state.get("decision") or {}).get("action_payload")
        if not isinstance(payload, dict):
            raise ValueError("action_payload is required for approval")
        response = interrupt({
            "kind": "action_approval",
            "case_id": state["case_id"],
            "run_id": state["run_id"],
            "message_key": "approve_prompt",
            "action_payload": payload,
            "allowed_responses": ["approve", "reject"],
        })
        if not isinstance(response, dict) or not isinstance(response.get("approved"), bool):
            raise ValueError("approval response must include a boolean approved field")
        if not response["approved"] and not str(response.get("reason", "")).strip():
            raise ValueError("rejection reason is required")
        review = {"approved": response["approved"]}
        if not response["approved"]:
            review["reason"] = str(response["reason"]).strip()
        return {
            "review_result": review,
            "action_payload": payload,
            "current_node": "action_review",
            "trace": [self._event(
                state, "action_review", "Human action review",
                "Action approval recorded" if response["approved"] else f"Reviewer rejected: {review['reason']}",
                runtime=runtime,
            )],
        }

    def execute_action(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        review = state.get("review_result") or {}
        if review.get("approved") is not True:
            raise PermissionError("action requires explicit approval")
        payload = (state.get("decision") or {}).get("action_payload")
        if not isinstance(payload, dict):
            raise ValueError("action_payload is required for execution")
        result = self.tools.execute_approved_action(payload, action_idempotency_key(payload))
        return {
            "action_result": result,
            "trace": [self._event(state, "execute_action", "Execute approved action", "Action gateway completed", runtime=runtime)],
        }

    def await_documents(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        payload = (state.get("decision") or {}).get("action_payload") or {}
        documents = list(payload.get("documents", []))
        response = interrupt({
            "kind": "document_submission",
            "case_id": state["case_id"],
            "run_id": state["run_id"],
            "requested_documents": documents,
            "allowed_responses": ["submit"],
        })
        return {
            "document_submission": response,
            "current_node": "await_documents",
            "trace": [self._event(state, "await_documents", "Await requested evidence", "Document submission received", runtime=runtime)],
        }

    def validate_submission(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        payload = (state.get("decision") or {}).get("action_payload") or {}
        required = set(payload.get("documents", []))
        response = state.get("document_submission") or {}
        documents = response.get("documents") if isinstance(response, dict) else None
        valid_items = isinstance(documents, list) and all(
            isinstance(item, dict) and isinstance(item.get("type"), str) and item.get("status") == "verified"
            for item in documents
        )
        submitted = {item["type"] for item in documents} if valid_items else set()
        valid = valid_items and required.issubset(submitted)
        update: dict[str, Any] = {
            "submission_valid": bool(valid),
            "trace": [self._event(state, "validate_submission", "Validate submitted evidence", "Evidence accepted" if valid else "Evidence incomplete", "complete" if valid else "degraded", runtime)],
        }
        if valid:
            update["submitted_documents"] = list(state.get("submitted_documents", [])) + list(documents)
        return update

    def increment_cycle(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        """Start the next bounded evidence cycle without erasing reducer-backed history."""
        if state.get("cycle_count", 1) >= state.get("max_cycles", 2):
            raise ValueError("maximum evidence cycles reached")
        update: dict[str, Any] = {"cycle_count": state.get("cycle_count", 1) + 1, "current_node": "load_customer"}
        for field in ("customer_facts", "document_facts", "screening_facts", "risk_facts", "facts", "citations", "policy_verdict", "policy_status", "proposal", "planner_status", "planner_attempts", "planner_error", "guardrail_override", "decision", "action_payload", "review_result", "action_result", "document_submission", "submission_valid", "operational_reason"):
            update[field] = None
        update.update({"policy_status": "pending", "planner_status": "pending", "planner_attempts": 0})
        return update

    def operational_review(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        reason = state.get("operational_reason") or "manual_review_required"
        decision = state.get("decision")
        update: dict[str, Any] = {}
        if not decision:
            decision = {
                "outcome": "MANUAL_REVIEW", "action": None, "action_payload": None,
                "risk_level": "UNKNOWN", "reason_key": reason, "reason_params": {},
            }
            update["decision"] = decision
        response = interrupt({
            "kind": "operational_review",
            "case_id": state["case_id"],
            "run_id": state["run_id"],
            "operational_reason": reason,
            "allowed_responses": ["acknowledge"],
        })
        if not isinstance(response, dict) or response.get("acknowledged") is not True:
            raise ValueError("operational review requires acknowledgement")
        update.update({
            "review_result": response,
            "action_payload": None,
            "current_node": "operational_review",
            "trace": [self._event(state, "operational_review", "Operational review acknowledged", reason, "degraded", runtime)],
        })
        return update

    def _finalize(self, state: WorkflowState, status: str, title: str, runtime: Any = None) -> dict[str, Any]:
        return {
            "workflow_status": status,
            "current_node": title,
            "trace": [self._event(state, title, title.replace("_", " ").title(), f"Workflow ended with {status}", runtime=runtime)],
        }

    def finalize(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        return self._finalize(state, "COMPLETED", "finalize", runtime)

    def finalize_blocked(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        return self._finalize(state, "BLOCKED", "finalize_blocked", runtime)

    def finalize_rejected(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        return self._finalize(state, "REJECTED", "finalize_rejected", runtime)

    def action_error_handler(self, state: WorkflowState, error: NodeError) -> dict[str, Any]:
        return {
            "action_result": {"status": "failed", "category": type(error.error).__name__},
            "operational_reason": "tool_unavailable",
            "trace": [self.event("execute_action", "Action unavailable", "Action retry policy exhausted; manual handling required", "degraded", state)],
        }
