"""LangGraph orchestration for the KYC exception agent.

Pipeline: ground -> retrieve -> reason -> guard -> review -> (execute).

  ground   deterministic, allowlisted read tools build the typed case facts.
  retrieve versioned policy citations are selected from the same facts.
  reason   the planner (LLM or fallback, see planner.py) proposes an outcome.
  guard    policy.guard() independently recomputes the mandated outcome and
           overrides the proposal if they disagree -- the actual safety net.
           It also builds the exact `action_payload` (case_id, action, and
           action-specific parameters such as the missing documents) that a
           reviewer approves and the gateway executes -- never just an
           action name.
  review   a write action pauses the graph with `interrupt()` for a human
           approval; a no-action outcome (CLEAR / ESCALATE_COMPLIANCE) skips
           straight through, since there is nothing to approve or it is
           already blocked.
  execute  runs only after approval, through an idempotent action gateway.

Graph state is kept to plain JSON-safe primitives (dict/list/str/float) so it
survives LangGraph's checkpoint serializer untouched and would work unchanged
behind a persistent checkpointer (Postgres/Redis) in production, not just the
in-memory one used here. Typed dataclasses -- and all display-language
rendering -- happen only at the boundary, in `_to_decision`, for callers
(CLI, HTTP API, Streamlit UI). Graph nodes never import `app.i18n` except
`review`, which needs a human-facing prompt at the moment it pauses.

One `KYCExceptionAgent` is meant to be shared by every caller in a process
(the HTTP server, a Streamlit session, the CLI). It lazily builds and caches
one compiled graph per distinct planner and keeps a single `_pending` map
keyed by LangGraph's own globally-unique interrupt id -- so approving a run
is never ambiguous, even when different requests picked different planners
for the same case and action.
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import asdict
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from . import i18n
from .domain import (
    AgentDecision,
    ApprovalRequest,
    LLMProposal,
    Outcome,
    PolicyCitation,
    ToolCall,
    TraceEvent,
)
from .planner import HeuristicPlanner, Planner, select_planner
from .policy import guard, tags_for
from .tools import DomainTools

ONTOLOGY_PATH = ["Customer", "KYCApplication", "Evidence", "RiskFinding", "Policy", "Resolution"]

TOOL_PLAN = [
    ("get_case", "Ground the case in authoritative customer data"),
    ("verify_documents", "Check identity evidence and liveness"),
    ("screen_sanctions", "Evaluate mandatory compliance stop conditions"),
    ("get_risk_profile", "Apply risk-tier policy"),
]


class State(TypedDict, total=False):
    case_id: str
    lang: str
    case_note: str
    facts: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    proposal: dict[str, Any]
    decision: dict[str, Any]
    trace: list[dict[str, Any]]
    action_result: dict[str, Any] | None


def _event(step: str, title: str, detail: str, status: str = "complete") -> dict[str, Any]:
    return asdict(TraceEvent(step, title, detail, status))


def _action_payload(case_id: str, verdict_action: str | None, facts: dict[str, Any]) -> dict[str, Any]:
    """The exact parameters a reviewer approves and the gateway executes --
    never just the action name."""
    payload: dict[str, Any] = {"case_id": case_id, "action": verdict_action}
    if verdict_action == "request_document":
        payload["documents"] = facts["verify_documents"]["missing_fields"]
    elif verdict_action == "open_manual_review":
        payload["evidence"] = facts["verify_documents"]
    return payload


def build_graph(tools: DomainTools, planner: Planner):
    fallback = HeuristicPlanner()

    def ground(state: State) -> dict[str, Any]:
        calls = [tools.call(name, purpose, {"case_id": state["case_id"]}) for name, purpose in TOOL_PLAN]
        facts = {name: call.output for (name, _), call in zip(TOOL_PLAN, calls)}
        trace = state.get("trace", []) + [
            _event("ground", "Build typed case graph", "Customer -> Application -> Evidence -> RiskFinding"),
            _event("act", "Call domain tools", f"Completed {len(calls)} allowlisted read operations"),
        ]
        return {
            "facts": facts,
            "case_note": tools.get_case_note(state["case_id"]),
            "tool_calls": [asdict(c) for c in calls],
            "trace": trace,
        }

    def retrieve(state: State) -> dict[str, Any]:
        citations = tools.retrieve_policy(tags_for(state["facts"]))
        trace = state["trace"] + [
            _event("retrieve", "Retrieve policy", f"Selected {len(citations)} versioned policy sections")
        ]
        return {"citations": [asdict(c) for c in citations], "trace": trace}

    def reason(state: State) -> dict[str, Any]:
        citations = [PolicyCitation(**c) for c in state["citations"]]
        args = (state["case_id"], state["facts"], citations, state["case_note"])
        try:
            proposal = planner.propose(*args)
            trace = state["trace"] + [
                _event("reason", f"Planner proposal ({proposal.model})", f"{proposal.outcome}: {proposal.rationale}")
            ]
        except Exception as exc:  # network/timeout/schema failure -- never crash a live run
            proposal = fallback.propose(*args)
            trace = state["trace"] + [
                _event(
                    "reason",
                    f"Planner call failed, used {fallback.name}",
                    f"{planner.__class__.__name__} raised {exc.__class__.__name__}: {exc}. "
                    f"Fell back to {proposal.outcome}: {proposal.rationale}",
                    "degraded",
                )
            ]
        return {"proposal": asdict(proposal), "trace": trace}

    def guard_node(state: State) -> dict[str, Any]:
        proposal = LLMProposal(**state["proposal"])
        result = guard(state["facts"], proposal)
        decision = {
            "outcome": result.verdict.outcome.value,
            "action": result.verdict.action,
            "risk_level": result.verdict.risk_level,
            "reason_key": result.verdict.reason_key,
            "reason_params": result.verdict.reason_params,
            "proposal_confidence": proposal.confidence,
            "model": proposal.model,
            "override_info": result.override_info,
            "action_payload": _action_payload(state["case_id"], result.verdict.action, state["facts"]),
        }
        detail = (
            i18n.render_override(result.override_info, "en")
            if result.override_info
            else "Model proposal matched the mandated policy verdict; no override needed."
        )
        trace = state["trace"] + [
            _event("guard", "Apply deterministic guardrail", detail, "override" if result.override_info else "complete")
        ]
        return {"decision": decision, "trace": trace}

    def review(state: State) -> dict[str, Any]:
        decision = state["decision"]
        lang = state.get("lang", "en")
        if not decision["action"]:
            why = "Outcome requires no automated action" if decision["outcome"] == Outcome.CLEAR.value else (
                "Automated action is prohibited for this outcome"
            )
            trace = state["trace"] + [_event("propose", "No side effect required", why)]
            return {"trace": trace, "action_result": None}

        approval_payload = interrupt(
            {
                "kind": "human_approval",
                "case_id": state["case_id"],
                "decision": decision,
                "action_payload": decision["action_payload"],
                "citations": state["citations"],
                "message": i18n.ui_text(lang, "approve_prompt"),
            }
        )
        if not approval_payload.get("approved"):
            trace = state["trace"] + [_event("approval", "Reviewer rejected", "No action executed")]
            return {"action_result": {"status": "rejected"}, "trace": trace}

        write_key = hashlib.sha256(f"{state['case_id']}:{decision['action']}".encode()).hexdigest()[:16]
        result = tools.execute_approved_action(decision["action_payload"], write_key)
        trace = state["trace"] + [_event("execute", "Execute approved action", result["ticket_id"])]
        return {"action_result": result, "trace": trace}

    graph = StateGraph(State)
    graph.add_node("ground", ground)
    graph.add_node("retrieve", retrieve)
    graph.add_node("reason", reason)
    graph.add_node("guard", guard_node)
    graph.add_node("review", review)
    graph.add_edge(START, "ground")
    graph.add_edge("ground", "retrieve")
    graph.add_edge("retrieve", "reason")
    graph.add_edge("reason", "guard")
    graph.add_edge("guard", "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=MemorySaver())


class KYCExceptionAgent:
    """LangGraph-orchestrated agent: a planner proposes, a deterministic
    guardrail verifies, and a human approves every write.

    Share one instance across every request in a process. Planner choice is
    a per-run argument, not part of construction, so switching planners
    between two runs -- or between a run and its later approval -- never
    targets the wrong graph.

    Every `run()`/`approve()` call caches the raw graph result under a
    `decision_id`. `relocalize()` re-renders that same cached result in a
    different language with zero graph execution -- no new tool calls, no
    new planner call, no new interrupt -- so switching the UI's language
    after a decision already exists never leaves stale English content
    behind, and never risks losing an in-flight approval by re-running.
    This is an in-memory demo cache with no eviction, matching `_pending`.
    """

    def __init__(self, tools: DomainTools | None = None) -> None:
        self.tools = tools or DomainTools()
        self._graphs: dict[str, Any] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._results: dict[str, dict[str, Any]] = {}
        # ThreadingHTTPServer serves concurrent requests against one shared
        # agent. This lock is coarse -- it serializes run/approve/relocalize
        # rather than locking per-thread_id -- which is the right tradeoff
        # for a demo (correctness over throughput) but is exactly the kind
        # of thing a production port must revisit with finer-grained
        # locking or an async graph runner.
        self._lock = threading.RLock()

    def _graph_for(self, planner_name: str | None, planner: Planner | None):
        with self._lock:
            if planner is not None:
                cache_key = f"instance:{id(planner)}"
                resolved = planner
            else:
                cache_key = planner_name or "auto"
                resolved = None
            if cache_key not in self._graphs:
                self._graphs[cache_key] = build_graph(self.tools, resolved or select_planner(planner_name))
            return self._graphs[cache_key]

    def run(self, case_id: str, planner_name: str | None = None, planner: Planner | None = None, lang: str = "en") -> AgentDecision:
        with self._lock:
            graph = self._graph_for(planner_name, planner)
            config = {"configurable": {"thread_id": f"{case_id}:{uuid4()}"}}
            result = graph.invoke({"case_id": case_id, "trace": [], "lang": lang}, config)
            decision_id = uuid4().hex
            self._results[decision_id] = {"case_id": case_id, "result": result, "config": config, "graph": graph}
            return self._to_decision(case_id, result, config, graph, lang, decision_id)

    def approve(self, approval_key: str, lang: str | None = None) -> AgentDecision:
        with self._lock:
            if approval_key not in self._pending:
                raise KeyError("Unknown or expired approval")
            pending = self._pending[approval_key]
            result = pending["graph"].invoke(Command(resume={"approved": True}), pending["config"])
            decision_id = pending["decision_id"]
            self._results[decision_id] = {
                "case_id": pending["case_id"], "result": result, "config": pending["config"], "graph": pending["graph"],
            }
            return self._to_decision(
                pending["case_id"], result, pending["config"], pending["graph"],
                lang or result.get("lang", "en"), decision_id,
            )

    def relocalize(self, decision_id: str, lang: str) -> AgentDecision:
        """Re-render an already-computed decision in a different language.
        Never touches the graph: no tool calls, no planner call, no new
        interrupt, and any pending approval for this decision stays valid."""
        with self._lock:
            if decision_id not in self._results:
                raise KeyError("Unknown or expired decision")
            cached = self._results[decision_id]
            return self._to_decision(cached["case_id"], cached["result"], cached["config"], cached["graph"], lang, decision_id)

    def has_pending(self, approval_key: str) -> bool:
        return approval_key in self._pending

    def _to_decision(
        self, case_id: str, result: dict[str, Any], config: dict[str, Any], graph: Any, lang: str, decision_id: str,
    ) -> AgentDecision:
        decision = result["decision"]
        summary = i18n.render_reason(decision["reason_key"], decision["reason_params"], lang)

        approval = None
        for interrupt_ in result.get("__interrupt__", ()):
            documents = decision["action_payload"].get("documents")
            documents_label = ", ".join(i18n.field_label(lang, f) for f in documents) if documents else None
            approval = ApprovalRequest(decision["action"], summary, interrupt_.value, interrupt_.id, documents_label)
            self._pending[interrupt_.id] = {
                "graph": graph, "config": config, "case_id": case_id, "decision_id": decision_id,
            }

        proposal = LLMProposal(**result["proposal"])
        override = i18n.render_override(decision["override_info"], lang) if decision["override_info"] else None
        rationale, rationale_translated = self._render_rationale(proposal, lang)

        return AgentDecision(
            case_id=case_id,
            decision_id=decision_id,
            outcome=Outcome(decision["outcome"]),
            outcome_label=i18n.outcome_label(lang, decision["outcome"]),
            summary=summary,
            proposal_confidence=decision["proposal_confidence"],
            risk_level=decision["risk_level"],
            risk_label=i18n.risk_label(lang, decision["risk_level"]),
            facts=i18n.render_facts(result["facts"], lang),
            citations=[
                PolicyCitation(c["policy_id"], c["version"], c["section"], i18n.policy_excerpt(c["policy_id"], c["excerpt"], lang))
                for c in result["citations"]
            ],
            tool_calls=[ToolCall(**c) for c in result["tool_calls"]],
            trace=[TraceEvent(**t) for t in result["trace"]],
            model=decision["model"],
            llm_rationale=rationale,
            lang=lang,
            rationale_translated=rationale_translated,
            guardrail_override=override,
            approval=approval,
            executed_action=result.get("action_result"),
            ontology_path=ONTOLOGY_PATH,
        )

    @staticmethod
    def _render_rationale(proposal: LLMProposal, lang: str) -> tuple[str, bool]:
        if proposal.rationale_key:
            return i18n.render_rationale_template(proposal.rationale_key, proposal.rationale_params or {}, lang), True
        if lang == "en":
            return proposal.rationale, True
        translated = i18n.translate_via_llm(proposal.rationale, lang)
        if translated:
            return translated, True
        return proposal.rationale, False
