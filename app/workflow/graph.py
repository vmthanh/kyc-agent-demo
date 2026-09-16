"""Compilation of the resumable, policy-first KYC workflow."""
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import NodeError as LangGraphNodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy

from ..planner import PlannerUnavailableError
from ..tools import TransientToolError
from .nodes import WorkflowNodes
from .routing import (
    route_after_action,
    route_after_evidence_gate,
    route_after_guard,
    route_after_planner,
    route_after_policy_precheck,
    route_after_review,
    route_after_safe_failure,
    route_after_submission,
)
from .state import WorkflowState


@dataclass(frozen=True)
class RetryPolicies:
    tool: RetryPolicy = field(default_factory=lambda: RetryPolicy(
        max_attempts=3, initial_interval=0.25, backoff_factor=2.0,
        max_interval=1.0, jitter=False, retry_on=TransientToolError,
    ))
    planner: RetryPolicy = field(default_factory=lambda: RetryPolicy(
        max_attempts=3, initial_interval=0.5, backoff_factor=2.0,
        max_interval=2.0, jitter=False, retry_on=PlannerUnavailableError,
    ))
    action: RetryPolicy = field(default_factory=lambda: RetryPolicy(
        max_attempts=3, initial_interval=0.25, backoff_factor=2.0,
        max_interval=1.0, jitter=False, retry_on=TransientToolError,
    ))


GROUNDING_NODES = ("load_customer", "verify_documents", "screen_watchlists", "load_risk")


def build_workflow_graph(
    tools: Any,
    planner: Any,
    checkpointer: Any | None = None,
    retry_policies: RetryPolicies | None = None,
):
    """Build and compile a graph bound to one tool/planner dependency set."""
    policies = retry_policies or RetryPolicies()
    nodes = WorkflowNodes(tools, planner)

    # `NodeError` is imported at module scope in nodes; retaining this helper
    # avoids exposing LangGraph's execution-only error object to domain code.
    from .nodes import NodeError

    def adapt(handler, goto: str):
        def wrapped(state: WorkflowState, error: LangGraphNodeError) -> Command:
            return Command(update=handler(state, NodeError(error.error, error.node)), goto=goto)
        return wrapped

    builder = StateGraph(WorkflowState)
    builder.add_node("intake", nodes.intake)
    def safe_grounding(name: str, field: str):
        fn = getattr(nodes, name)
        def wrapped(state: WorkflowState):
            last: BaseException | None = None
            for _ in range(3):
                try:
                    return fn(state)
                except TransientToolError as exc:
                    last = exc
            return nodes.tool_error_handler(field)(state, NodeError(last or TransientToolError("unavailable"), name))
        return wrapped
    for name in GROUNDING_NODES:
        field = {
            "load_customer": "customer_facts", "verify_documents": "document_facts",
            "screen_watchlists": "screening_facts", "load_risk": "risk_facts",
        }[name]
        builder.add_node(name, safe_grounding(name, field), retry_policy=policies.tool,
                         error_handler=adapt(nodes.tool_error_handler(field), "evidence_gate"))
    builder.add_node("evidence_gate", nodes.evidence_gate)
    builder.add_node("retrieve_policy", nodes.retrieve_policy)
    builder.add_node("policy_precheck", nodes.policy_precheck)
    builder.add_node("openrouter_reason", nodes.openrouter_reason,
                     retry_policy=policies.planner, error_handler=adapt(nodes.planner_error_handler, "safe_failure"))
    builder.add_node("reconcile_guard", nodes.reconcile_guard)
    builder.add_node("action_review", nodes.action_review)
    builder.add_node("execute_action", nodes.execute_action,
                     retry_policy=policies.action, error_handler=adapt(nodes.action_error_handler, "operational_review"))
    builder.add_node("await_documents", nodes.await_documents)
    builder.add_node("validate_submission", nodes.validate_submission)
    builder.add_node("increment_cycle", nodes.increment_cycle)
    builder.add_node("safe_failure", nodes.safe_failure)
    builder.add_node("operational_review", nodes.operational_review)
    builder.add_node("finalize", nodes.finalize)
    builder.add_node("finalize_blocked", nodes.finalize_blocked)
    builder.add_node("finalize_rejected", nodes.finalize_rejected)

    builder.add_edge(START, "intake")
    for name in GROUNDING_NODES:
        builder.add_edge("intake", name)
    builder.add_edge(list(GROUNDING_NODES), "evidence_gate")
    builder.add_conditional_edges("evidence_gate", route_after_evidence_gate)
    builder.add_edge("retrieve_policy", "policy_precheck")
    builder.add_conditional_edges("policy_precheck", route_after_policy_precheck)
    builder.add_conditional_edges("openrouter_reason", route_after_planner)
    builder.add_conditional_edges("safe_failure", route_after_safe_failure)
    builder.add_conditional_edges("reconcile_guard", route_after_guard)
    builder.add_conditional_edges("action_review", route_after_review)
    builder.add_conditional_edges("execute_action", route_after_action)
    builder.add_edge("await_documents", "validate_submission")
    builder.add_conditional_edges("validate_submission", route_after_submission)
    for name in GROUNDING_NODES:
        builder.add_edge("increment_cycle", name)
    builder.add_edge("operational_review", "finalize")
    builder.add_edge("finalize", END)
    builder.add_edge("finalize_blocked", END)
    builder.add_edge("finalize_rejected", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())
