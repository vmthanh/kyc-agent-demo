"""Compilation of the resumable, policy-first KYC workflow.

Note: an earlier version of this module nested `collect_evidence`, `assess`,
and `document_loop` as real LangGraph subgraphs to shrink the top-level
picture further. That broke `trace`/`tool_calls`/`tool_errors`: LangGraph
1.2.11 re-applies an `operator.add` reducer's already-accumulated value
every time execution crosses into a nested subgraph node, so those
append-only fields doubled (then quadrupled, ...) on each subgraph entry.
This stays a single flat `StateGraph`; the simplification that's still
safe here is collapsing `safe_failure` into `reconcile_guard` (the LLM
node now fails closed on its own) and the three finalize variants into one
node keyed by a `final_outcome` field.
"""
from dataclasses import dataclass, field
import os
import time
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import NodeError as LangGraphNodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy

from ..tools import TransientToolError
from .nodes import NodeError, WorkflowNodes
from .routing import (
    route_after_action,
    route_after_evidence_gate,
    route_after_guard,
    route_after_policy_precheck,
    route_after_review,
    route_after_submission,
)
from .state import WorkflowState


@dataclass(frozen=True)
class RetryPolicies:
    """The LLM node (`openrouter_reason`) fails closed on its own and never
    raises, so it needs no entry here -- only nodes that still rely on
    LangGraph's native retry_policy/error_handler wiring do."""

    tool: RetryPolicy = field(default_factory=lambda: RetryPolicy(
        max_attempts=3, initial_interval=0.25, backoff_factor=2.0,
        max_interval=1.0, jitter=False, retry_on=TransientToolError,
    ))
    action: RetryPolicy = field(default_factory=lambda: RetryPolicy(
        max_attempts=3, initial_interval=0.25, backoff_factor=2.0,
        max_interval=1.0, jitter=False, retry_on=TransientToolError,
    ))


GROUNDING_NODES = ("load_customer", "verify_documents", "screen_watchlists", "load_risk")
GROUNDING_FIELDS = {
    "load_customer": "customer_facts", "verify_documents": "document_facts",
    "screen_watchlists": "screening_facts", "load_risk": "risk_facts",
}


def _adapt(handler, goto: str):
    def wrapped(state: WorkflowState, error: LangGraphNodeError) -> Command:
        return Command(update=handler(state, NodeError(error.error, error.node)), goto=goto)
    return wrapped


def _safe_grounding(nodes: WorkflowNodes, policies: RetryPolicies, name: str, field_name: str):
    """Manual retry-and-fail-closed wrapper: catches `TransientToolError`
    itself and hands off to `tool_error_handler`, so it never raises and the
    `retry_policy`/`error_handler` registered alongside it are a static
    safety net only."""
    fn = getattr(nodes, name)

    def wrapped(state: WorkflowState):
        last: BaseException | None = None
        attempts = max(1, int(policies.tool.max_attempts))
        for attempt in range(1, attempts + 1):
            try:
                update = fn(state)
                if attempt > 1 and update.get("trace"):
                    update["trace"][-1]["attempt"] = attempt
                return update
            except TransientToolError as exc:
                last = exc
                if attempt < attempts:
                    delay = min(policies.tool.max_interval, policies.tool.initial_interval * (policies.tool.backoff_factor ** (attempt - 1)))
                    if delay:
                        time.sleep(delay)
        update = nodes.tool_error_handler(field_name)(state, NodeError(last or TransientToolError("unavailable"), name))
        if update.get("trace"):
            update["trace"][-1]["attempt"] = attempts
        return update
    return wrapped


def build_workflow_graph(
    tools: Any,
    planner: Any,
    checkpointer: Any | None = None,
    retry_policies: RetryPolicies | None = None,
):
    """Build and compile a graph bound to one tool/planner dependency set."""
    policies = retry_policies or RetryPolicies()
    nodes = WorkflowNodes(tools, planner)

    builder = StateGraph(WorkflowState)
    builder.add_node("intake", nodes.intake)
    for name in GROUNDING_NODES:
        field_name = GROUNDING_FIELDS[name]
        builder.add_node(name, _safe_grounding(nodes, policies, name, field_name), retry_policy=policies.tool,
                         error_handler=_adapt(nodes.tool_error_handler(field_name), "evidence_gate"))
    builder.add_node("evidence_gate", nodes.evidence_gate)
    builder.add_node("retrieve_policy", nodes.retrieve_policy)
    builder.add_node("policy_precheck", nodes.policy_precheck)
    builder.add_node("openrouter_reason", nodes.openrouter_reason)
    builder.add_node("reconcile_guard", nodes.reconcile_guard)
    builder.add_node("action_review", nodes.action_review)
    builder.add_node("execute_action", nodes.execute_action,
                     retry_policy=policies.action, error_handler=_adapt(nodes.action_error_handler, "operational_review"))
    builder.add_node("await_documents", nodes.await_documents)
    builder.add_node("validate_submission", nodes.validate_submission)
    builder.add_node("increment_cycle", nodes.increment_cycle)
    builder.add_node("operational_review", nodes.operational_review)
    builder.add_node("finalize", nodes.finalize)

    builder.add_edge(START, "intake")
    for name in GROUNDING_NODES:
        builder.add_edge("intake", name)
    builder.add_edge(list(GROUNDING_NODES), "evidence_gate")
    builder.add_conditional_edges("evidence_gate", route_after_evidence_gate,
                                  {"sufficient": "retrieve_policy", "insufficient": "operational_review"})
    builder.add_edge("retrieve_policy", "policy_precheck")
    builder.add_conditional_edges("policy_precheck", route_after_policy_precheck,
                                  {"proceed": "openrouter_reason", "bail": "operational_review"})
    builder.add_edge("openrouter_reason", "reconcile_guard")
    builder.add_conditional_edges("reconcile_guard", route_after_guard, {
        "clear": "finalize", "blocked": "finalize",
        "escalate": "operational_review", "needs_action": "action_review",
    })
    builder.add_conditional_edges("action_review", route_after_review, {"approved": "execute_action", "rejected": "finalize"})
    builder.add_conditional_edges("execute_action", route_after_action,
                                  {"docs_requested": "await_documents", "done": "finalize", "escalate": "operational_review"})
    builder.add_edge("await_documents", "validate_submission")
    builder.add_conditional_edges("validate_submission", route_after_submission,
                                  {"retry": "await_documents", "done": "increment_cycle", "exhausted": "operational_review"})
    for name in GROUNDING_NODES:
        builder.add_edge("increment_cycle", name)
    builder.add_edge("operational_review", "finalize")
    builder.add_edge("finalize", END)
    graph = builder.compile(checkpointer=checkpointer or MemorySaver())
    if os.getenv("KYC_PRINT_GRAPH") == "1":
        print(graph_mermaid(graph))
    return graph


def graph_mermaid(graph) -> str:
    """The compiled graph as mermaid syntax (paste into mermaid.live or an editor preview).
    Printed on build only when KYC_PRINT_GRAPH=1, so CLI/eval output stays clean JSON.

    Error-handler nodes are only reachable via runtime Command(goto=...), so they
    have no static edges. Left in, they'd show up as disconnected floating boxes,
    so they're excluded before rendering.
    """
    g = graph.get_graph()
    connected_ids = {edge.source for edge in g.edges} | {edge.target for edge in g.edges}
    for node_id in list(g.nodes):
        if node_id not in connected_ids:
            g.remove_node(g.nodes[node_id])
    return g.draw_mermaid()
