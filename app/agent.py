"""Public façade for the resumable LangGraph KYC workflow."""
from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from . import i18n
from .domain import AgentDecision, ApprovalRequest, LLMProposal, Outcome, PendingTask, PendingTaskKind, PolicyCitation, ToolCall, TraceEvent, WorkflowStatus
from .ontology import load_ontology
from .planner import Planner, select_planner
from .tools import DomainTools
from .workflow.graph import build_workflow_graph
from .workflow.state import initial_state

class KYCExceptionAgent:
    def __init__(self, tools: DomainTools | None = None, checkpointer: Any | None = None) -> None:
        self.tools = tools or DomainTools()
        self.checkpointer = checkpointer
        self._graphs: dict[str, Any] = {}
        self._pending_tasks: dict[str, dict[str, Any]] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def _graph_for(self, planner_name: str | None, planner: Planner | None):
        if planner is not None:
            key, resolved = f"instance:{id(planner)}", planner
        else:
            key, resolved = planner_name or "auto", select_planner(planner_name)
        if key not in self._graphs:
            self._graphs[key] = build_workflow_graph(
                self.tools, resolved, checkpointer=self.checkpointer
            )
        return self._graphs[key]

    def run(
        self,
        case_id: str,
        planner_name: str | None = None,
        planner: Planner | None = None,
        lang: str = "en",
        planner_mode: str | None = None,
    ) -> AgentDecision:
        with self._lock:
            # ``planner_mode`` is the public API name; retain planner_name for
            # callers of the original façade and for injected planner objects.
            selected_planner = planner_mode or planner_name
            graph = self._graph_for(selected_planner, planner)
            decision_id = uuid4().hex
            thread_id = f"{case_id}:{uuid4()}"
            config = {"configurable": {"thread_id": thread_id}}
            mode = selected_planner if selected_planner in {"normal", "compromised_demo"} else "normal"
            result = graph.invoke(initial_state(case_id, thread_id, lang, mode), config)
            self._cache_result(decision_id, case_id, result, config, graph)
            return self._to_decision(case_id, result, config, graph, lang, decision_id)

    def resume(self, interrupt_key: str, response: dict[str, Any], lang: str | None = None) -> AgentDecision:
        with self._lock:
            pending = self._pending_tasks.get(interrupt_key)
            if pending is None:
                raise KeyError("Unknown or already resolved interrupt")
            kind = (pending.get("value") or {}).get("kind")
            if kind == PendingTaskKind.ACTION_APPROVAL.value:
                if not isinstance(response, dict) or not isinstance(response.get("approved"), bool):
                    raise ValueError("approval response must include a boolean approved field")
                if response["approved"] is False and not str(response.get("reason", "")).strip():
                    raise ValueError("rejection reason is required")
            elif kind == PendingTaskKind.OPERATIONAL_REVIEW.value:
                if not isinstance(response, dict) or response.get("acknowledged") is not True:
                    raise ValueError("operational review requires acknowledgement")
            result = pending["graph"].invoke(Command(resume=response), pending["config"])
            self._pending_tasks.pop(interrupt_key, None)
            self._cache_result(pending["decision_id"], pending["case_id"], result, pending["config"], pending["graph"])
            return self._to_decision(pending["case_id"], result, pending["config"], pending["graph"], lang or result.get("lang", "en"), pending["decision_id"])

    def approve(self, approval_key: str, lang: str | None = None) -> AgentDecision:
        return self.resume(approval_key, {"approved": True}, lang=lang)

    def reject(self, approval_key: str, reason: str, lang: str | None = None) -> AgentDecision:
        reason = reason.strip()
        if not reason:
            raise ValueError("A rejection reason is required")
        return self.resume(approval_key, {"approved": False, "reason": reason}, lang=lang)

    def relocalize(self, decision_id: str, lang: str) -> AgentDecision:
        with self._lock:
            cached = self._results.get(decision_id)
            if cached is None:
                raise KeyError("Unknown or expired decision")
            return self._to_decision(cached["case_id"], cached["result"], cached["config"], cached["graph"], lang, decision_id)

    def has_pending(self, interrupt_key: str) -> bool:
        with self._lock:
            return interrupt_key in self._pending_tasks

    def _cache_result(self, decision_id: str, case_id: str, result: dict[str, Any], config: dict[str, Any], graph: Any) -> None:
        self._results[decision_id] = {"case_id": case_id, "result": result, "config": config, "graph": graph}

    def _to_decision(self, case_id: str, result: dict[str, Any], config: dict[str, Any], graph: Any, lang: str, decision_id: str) -> AgentDecision:
        decision = result.get("decision") or {
            "outcome": Outcome.MANUAL_REVIEW.value, "action": None, "risk_level": "UNKNOWN",
            "reason_key": result.get("operational_reason", "manual_review_required"), "reason_params": {},
            "action_payload": None, "guardrail_override": None,
        }
        reason_key = decision.get("reason_key", "manual_review_required")
        if reason_key not in i18n.REASON_TEMPLATES:
            reason_key = "tool_unavailable" if reason_key == "tool_unavailable" else "ai_unavailable"
        summary = i18n.render_reason(reason_key, decision.get("reason_params", {}), lang)
        pending_task: PendingTask | None = None
        approval: ApprovalRequest | None = None
        for interrupt_ in result.get("__interrupt__", ()):
            raw = interrupt_.value if isinstance(interrupt_.value, dict) else {}
            try:
                task_kind = PendingTaskKind(raw.get("kind", PendingTaskKind.OPERATIONAL_REVIEW.value))
            except ValueError:
                task_kind = PendingTaskKind.OPERATIONAL_REVIEW
            if task_kind is PendingTaskKind.ACTION_APPROVAL:
                title, message = i18n.ui_text(lang, "approval_required"), i18n.ui_text(lang, "approve_prompt")
                approval = ApprovalRequest(decision.get("action") or "", summary, raw, interrupt_.id,
                    ", ".join(i18n.field_label(lang, f) for f in (raw.get("action_payload") or {}).get("documents", [])) or None)
            elif task_kind is PendingTaskKind.DOCUMENT_SUBMISSION:
                title, message = i18n.ui_text(lang, "pending_document_submission"), i18n.ui_text(lang, "pending_document_submission")
            else:
                title, message = i18n.ui_text(lang, "operational_handoff"), summary
            pending_task = PendingTask(task_kind, interrupt_.id, title, message, raw, list(raw.get("allowed_responses", [])))
            self._pending_tasks[interrupt_.id] = {"graph": graph, "config": config, "case_id": case_id, "decision_id": decision_id, "value": raw}

        proposal_raw = result.get("proposal")
        proposal = LLMProposal(**proposal_raw) if proposal_raw else None
        override_info = decision.get("guardrail_override") or decision.get("override_info")
        override = i18n.render_override(override_info, lang) if override_info else None
        rationale, translated = self._render_rationale(proposal, lang)
        action_result = result.get("action_result") or {}
        review_result = result.get("review_result") or {}
        if action_result.get("status") == "rejected":
            review_view = action_result
        elif review_result.get("approved") is False:
            review_view = {"status": "rejected", "reason": review_result.get("reason", "")}
        else:
            review_view = None
        status = WorkflowStatus(result.get("workflow_status", WorkflowStatus.COMPLETED.value))
        if pending_task:
            status = {PendingTaskKind.ACTION_APPROVAL: WorkflowStatus.AWAITING_APPROVAL, PendingTaskKind.DOCUMENT_SUBMISSION: WorkflowStatus.AWAITING_DOCUMENTS, PendingTaskKind.OPERATIONAL_REVIEW: WorkflowStatus.AWAITING_OPERATIONS}[pending_task.kind]
        facts = result.get("facts") or {}
        outcome_value = decision.get("outcome", Outcome.MANUAL_REVIEW.value)
        ontology = load_ontology()
        ontology_path = ontology.path_for(decision.get("rule_id"), decision.get("rule_version"), outcome_value)
        rule_view = ontology.rule_view(decision.get("rule_id"), decision.get("rule_version"))
        return AgentDecision(
            case_id=case_id, decision_id=decision_id, outcome=Outcome(decision.get("outcome", Outcome.MANUAL_REVIEW.value)),
            outcome_label=i18n.outcome_label(lang, decision.get("outcome", Outcome.MANUAL_REVIEW.value)), summary=summary,
            proposal_confidence=proposal.confidence if proposal else None, risk_level=decision.get("risk_level", "UNKNOWN"), risk_label=i18n.risk_label(lang, decision.get("risk_level", "UNKNOWN")),
            facts=i18n.render_facts(facts, lang), citations=[PolicyCitation(c["policy_id"], c["version"], c["section"], i18n.policy_excerpt(c["policy_id"], c["excerpt"], lang)) for c in result.get("citations", [])],
            tool_calls=[ToolCall(**c) for c in result.get("tool_calls", [])], trace=[TraceEvent(**t) for t in result.get("trace", [])],
            model=proposal.model if proposal else None, llm_rationale=rationale, lang=lang, rationale_translated=translated,
            guardrail_override=override, approval=approval, executed_action=action_result if action_result.get("status") == "executed" else None,
            review_result=review_view, ontology_path=ontology_path, workflow_status=status,
            current_node=("action_review" if pending_task and pending_task.kind is PendingTaskKind.ACTION_APPROVAL else
                          "await_documents" if pending_task and pending_task.kind is PendingTaskKind.DOCUMENT_SUBMISSION else
                          "operational_review" if pending_task else result.get("current_node", "finalize")),
            cycle_count=result.get("cycle_count", 1), max_cycles=result.get("max_cycles", 2), pending_task=pending_task,
            planner_attempts=result.get("planner_attempts", 0), planner_usage=proposal.usage if proposal else {},
            planner_mode=result.get("planner_mode", "normal"), rule=rule_view,
        )

    @staticmethod
    def _render_rationale(proposal: LLMProposal | None, lang: str) -> tuple[str | None, bool]:
        if proposal is None:
            return None, True
        if proposal.rationale_key:
            return i18n.render_rationale_template(proposal.rationale_key, proposal.rationale_params or {}, lang), True
        if lang == "en":
            return proposal.rationale, True
        translated = i18n.translate_via_llm(proposal.rationale, lang)
        return (translated, True) if translated else (proposal.rationale, False)
