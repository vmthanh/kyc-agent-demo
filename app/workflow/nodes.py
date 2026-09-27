"""Small, dependency-bound nodes for the policy-first workflow.

The node methods deliberately return *updates*, rather than mutating the
incoming state.  This matters for the four parallel reads: each owns one
fact field and append-only fields contribute only their own reducer value.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

from langgraph.types import interrupt

from ..domain import LLMProposal, Outcome, PolicyCitation
from ..planner import Planner, PlannerUnavailableError
from ..ontology import load_ontology
from ..policy import PolicyVerdict, evaluate, guard_verdict, tags_for
from ..tools import DomainTools, action_idempotency_key
from .state import WorkflowState

# The LLM node fails closed on its own (see `openrouter_reason`); it never
# raises past this module, so no LangGraph retry_policy/error_handler is
# wired for it at the graph level.
PLANNER_MAX_ATTEMPTS = 3
PLANNER_INITIAL_INTERVAL = 0.5
PLANNER_BACKOFF_FACTOR = 2.0
PLANNER_MAX_INTERVAL = 2.0

MAX_SUBMISSION_ATTEMPTS = 3

# Dana-style operating loop: See (grounded reads) -> Think (policy + model) ->
# Act (governed writes and human tasks) -> Reflect (close out and learn).
PHASE_BY_STEP = {
    **dict.fromkeys(("intake", "load_customer", "verify_documents", "screen_watchlists", "load_risk", "evidence_gate"), "SEE"),
    **dict.fromkeys(("retrieve_policy", "policy_precheck", "openrouter_reason", "reconcile_guard"), "THINK"),
    **dict.fromkeys(("action_review", "execute_action", "await_documents", "validate_submission",
                     "increment_cycle", "operational_review"), "ACT"),
    "finalize": "REFLECT",
}
# Governed write: the model PROPOSEs, the ontology guard VERIFYs, and only a
# human-approved, idempotent gateway call COMMITs.
GOV_STAGE_BY_STEP = {
    "openrouter_reason": "PROPOSE",
    "reconcile_guard": "VERIFY",
    "action_review": "COMMIT",
    "execute_action": "COMMIT",
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
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "step": step,
            "title": title,
            "detail": detail,
            "status": status,
            "cycle": state.get("cycle_count", 1),
            "duration_ms": 0,
            "attempt": WorkflowNodes._attempt(runtime),
            "metadata": dict(metadata or {}),
            "phase": PHASE_BY_STEP.get(step),
            "gov_stage": GOV_STAGE_BY_STEP.get(step),
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
                "evidence_ok": False,
                "operational_reason": "tool_unavailable",
                "trace": [self._event(state, "evidence_gate", "Evidence incomplete", "Authoritative grounding is unavailable", "degraded", runtime)],
            }
        return {
            "evidence_ok": True,
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
        # The matched rule names the policy it implements; precheck fails closed
        # if retrieval did not surface that exact policy.
        required = verdict.cites or load_ontology().required_policy(verdict.reason_key)
        citation_ids = {
            citation.get("policy_id") if isinstance(citation, dict) else citation.policy_id
            for citation in state.get("citations", [])
        }
        covered = required is not None and required in citation_ids
        grouped: dict[str, set[str]] = {}
        for citation in state.get("citations", []):
            grouped.setdefault(citation["policy_id"], set()).add(citation["excerpt"])
        contradictory = any(len(excerpts) > 1 for excerpts in grouped.values())
        if not covered or contradictory:
            return {
                "policy_verdict": serialized,
                "policy_status": "failed",
                "operational_reason": "policy_unavailable",
                "trace": [self._event(state, "policy_precheck", "Policy unavailable", "Required policy coverage is missing", "degraded", runtime)],
            }
        return {
            "policy_verdict": serialized,
            "policy_status": "ok",
            "trace": [self._event(state, "policy_precheck", "Compute mandatory policy outcome", f"Precheck requires {verdict.outcome.value} via rule {verdict.rule_id}@{verdict.rule_version} ({verdict.cites})", runtime=runtime)],
        }

    def openrouter_reason(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        """Fail closed on its own: retries transient planner outages in-node and
        never raises, so `reconcile_guard` can fold an unavailable model into the
        deterministic verdict without a dedicated error-routing node."""
        citations = [PolicyCitation(**citation) for citation in state.get("citations", [])]
        last_error: BaseException | None = None
        for attempt in range(1, PLANNER_MAX_ATTEMPTS + 1):
            try:
                proposal = self.planner.propose(
                    state["case_id"], state["facts"], citations, self.tools.get_case_note(state["case_id"]),
                )
                return {
                    "proposal": asdict(proposal),
                    "planner_status": "ok",
                    "planner_attempts": attempt,
                    "trace": [self._event(state, "openrouter_reason", f"Planner proposal ({proposal.model})", proposal.outcome, runtime=runtime)],
                }
            except PlannerUnavailableError as exc:
                last_error = exc
                if attempt < PLANNER_MAX_ATTEMPTS:
                    delay = min(PLANNER_MAX_INTERVAL, PLANNER_INITIAL_INTERVAL * (PLANNER_BACKOFF_FACTOR ** (attempt - 1)))
                    if delay:
                        time.sleep(delay)
        return {
            "planner_status": "failed",
            "planner_attempts": PLANNER_MAX_ATTEMPTS,
            "planner_error": {"category": type(last_error).__name__, "node": "openrouter_reason"},
            "trace": [self._event(state, "openrouter_reason", "OpenRouter retries exhausted", "Manual handling required", "degraded", runtime)],
        }

    def reconcile_guard(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        if state.get("planner_status") == "failed":
            return self._reconcile_without_proposal(state, runtime)
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
            # Bind the approval to the exact rule version: a rule change yields a
            # new payload, hence a new idempotency key and a fresh approval.
            if result.verdict.rule_id:
                payload["rule"] = f"{result.verdict.rule_id}@{result.verdict.rule_version}"
            if result.verdict.action == "request_document":
                payload["documents"] = sorted(result.verdict.reason_params.get("fields", []))
        decision = {
            "outcome": result.verdict.outcome.value,
            "action": result.verdict.action,
            "risk_level": result.verdict.risk_level,
            "reason_key": result.verdict.reason_key,
            "reason_params": result.verdict.reason_params,
            "rule_id": result.verdict.rule_id,
            "rule_version": result.verdict.rule_version,
            "action_payload": payload,
            "guardrail_override": result.override_info,
        }
        if decision["outcome"] == "REQUEST_EVIDENCE" and state.get("cycle_count", 1) >= state.get("max_cycles", 2):
            decision["reason_key"] = "cycle_exhausted"
            decision["reason_params"] = {}
        update = {
            "decision": decision,
            "action_payload": payload,
            "guardrail_override": result.override_info,
            "trace": [self._event(state, "reconcile_guard", "Apply deterministic guard", "Model proposal reconciled with policy", "override" if result.override_info else "complete", runtime)],
        }
        if decision["outcome"] == Outcome.ESCALATE_COMPLIANCE.value:
            update["final_outcome"] = "BLOCKED"
        return update

    def _reconcile_without_proposal(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        """Fail-closed path for a planner outage: the sanctions hard stop
        (already computed by `policy_precheck`) survives untouched; anything
        else degrades to a manual review with no automated action."""
        verdict = dict(state.get("policy_verdict") or {})
        if verdict.get("outcome") != Outcome.ESCALATE_COMPLIANCE.value:
            verdict.update({"outcome": Outcome.MANUAL_REVIEW.value, "action": None, "reason_key": "ai_unavailable", "reason_params": {},
                            "rule_id": None, "rule_version": None})
        decision = {
            "outcome": verdict.get("outcome"),
            "action": verdict.get("action"),
            "risk_level": verdict.get("risk_level", "UNKNOWN"),
            "reason_key": verdict.get("reason_key"),
            "reason_params": verdict.get("reason_params", {}),
            "rule_id": verdict.get("rule_id"),
            "rule_version": verdict.get("rule_version"),
            "action_payload": None,
            "guardrail_override": None,
        }
        update: dict[str, Any] = {
            "decision": decision,
            "action_payload": None,
            "guardrail_override": None,
            "trace": [self._event(state, "reconcile_guard", "Safe failure route", "Deterministic policy remains authoritative", "degraded", runtime)],
        }
        if decision["outcome"] == Outcome.ESCALATE_COMPLIANCE.value:
            update["final_outcome"] = "BLOCKED"
        else:
            update["operational_reason"] = decision["reason_key"]
        return update

    def tool_error_handler(self, field: str):
        def handler(state: WorkflowState, error: NodeError) -> dict[str, Any]:
            return {
                field: None,
                "tool_errors": [{"node": error.node, "category": type(error.error).__name__}],
                "trace": [self.event(error.node, "Authoritative tool unavailable", "Manual handling required", "degraded", state)],
            }
        return handler

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
        update = {
            "review_result": review,
            "action_payload": payload,
            "current_node": "action_review",
            "trace": [self._event(
                state, "action_review", "Human action review",
                "Action approval recorded" if response["approved"] else f"Reviewer rejected: {review['reason']}",
                runtime=runtime, metadata={"approved": response["approved"]},
            )],
        }
        if not response["approved"]:
            update["final_outcome"] = "REJECTED"
        return update

    def execute_action(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        review = state.get("review_result") or {}
        if review.get("approved") is not True:
            raise PermissionError("action requires explicit approval")
        payload = (state.get("decision") or {}).get("action_payload")
        if not isinstance(payload, dict):
            raise ValueError("action_payload is required for execution")
        result = self.tools.execute_approved_action(payload, action_idempotency_key(payload))
        update = {
            "action_result": result,
            "trace": [self._event(state, "execute_action", "Execute approved action", "Action gateway completed", runtime=runtime)],
        }
        if result.get("status") == "executed" and result.get("action") == "request_document":
            update["submission_attempts"] = 0
            update["submission_exhausted"] = False
        return update

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
        attempts = int(state.get("submission_attempts", 0)) + 1
        exhausted = not valid and attempts >= MAX_SUBMISSION_ATTEMPTS
        detail = "Evidence accepted" if valid else ("Resubmission attempts exhausted" if exhausted else "Evidence incomplete")
        update: dict[str, Any] = {
            "submission_valid": bool(valid),
            "submission_attempts": attempts,
            "submission_exhausted": exhausted,
            "trace": [self._event(state, "validate_submission", "Validate submitted evidence", detail, "complete" if valid else "degraded", runtime)],
        }
        if valid:
            update["submitted_documents"] = list(state.get("submitted_documents", [])) + list(documents)
        if exhausted:
            update["operational_reason"] = "submission_attempts_exhausted"
            existing_decision = state.get("decision")
            if existing_decision:
                update["decision"] = {**existing_decision, "reason_key": "submission_attempts_exhausted", "reason_params": {}}
        return update

    def increment_cycle(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        """Start the next bounded evidence cycle without erasing reducer-backed history."""
        if state.get("cycle_count", 1) >= state.get("max_cycles", 2):
            raise ValueError("maximum evidence cycles reached")
        update: dict[str, Any] = {"cycle_count": state.get("cycle_count", 1) + 1, "current_node": "load_customer"}
        for field in ("customer_facts", "document_facts", "screening_facts", "risk_facts", "facts", "citations", "policy_verdict", "policy_status", "proposal", "planner_status", "planner_attempts", "planner_error", "guardrail_override", "decision", "action_payload", "review_result", "action_result", "document_submission", "submission_valid", "operational_reason", "evidence_ok", "final_outcome"):
            update[field] = None
        update.update({
            "policy_status": "pending", "planner_status": "pending", "planner_attempts": 0,
            "submission_attempts": 0, "submission_exhausted": False,
        })
        return update

    def operational_review(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        reason = state.get("operational_reason") or "manual_review_required"
        decision = state.get("decision")
        exhausted_evidence = bool(decision and decision.get("outcome") == "REQUEST_EVIDENCE" and state.get("cycle_count", 1) >= state.get("max_cycles", 2))
        relabel = "cycle_exhausted" if exhausted_evidence else "submission_attempts_exhausted" if decision and state.get("submission_exhausted") else None
        if relabel:
            reason = relabel
            decision = {**decision, "reason_key": reason, "reason_params": {}}
        update: dict[str, Any] = {}
        if relabel:
            update["decision"] = decision
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

    def finalize(self, state: WorkflowState, runtime: Any = None) -> dict[str, Any]:
        """One terminal node for every outcome. `final_outcome` is written by
        whichever phase decided the case (reconcile_guard for BLOCKED,
        action_review for REJECTED); anything else defaults to COMPLETED,
        which covers CLEAR, a completed action, and every operational-review
        handoff."""
        status = state.get("final_outcome") or "COMPLETED"
        review = state.get("review_result") or {}
        signal = "rejection" if review.get("approved") is False else "operational_handoff" if review.get("acknowledged") else None
        detail = f"Workflow ended with {status}" + ("; reviewer signal captured for Reflect" if signal else "")
        return {
            "workflow_status": status,
            "current_node": "finalize",
            "trace": [self._event(state, "finalize", "Finalize decision", detail, runtime=runtime,
                                  metadata={"review_signal": signal} if signal else None)],
        }

    def action_error_handler(self, state: WorkflowState, error: NodeError) -> dict[str, Any]:
        return {
            "action_result": {"status": "failed", "category": type(error.error).__name__},
            "operational_reason": "tool_unavailable",
            "trace": [self.event("execute_action", "Action unavailable", "Action retry policy exhausted; manual handling required", "degraded", state)],
        }
