"""Small, dependency-bound nodes for the policy-first workflow.

The node methods deliberately return *updates*, rather than mutating the
incoming state.  This matters for the four parallel reads: each owns one
fact field and append-only fields contribute only their own reducer value.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..domain import LLMProposal, Outcome, PolicyCitation
from ..planner import Planner
from ..policy import PolicyVerdict, evaluate, guard_verdict, tags_for
from ..tools import DomainTools
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
