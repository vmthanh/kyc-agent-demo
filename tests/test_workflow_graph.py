import unittest

from app.agent import KYCExceptionAgent
from app.domain import Outcome, PendingTaskKind, WorkflowStatus
from app.planner import HeuristicPlanner, PlannerUnavailableError
from app.tools import DomainTools, TransientToolError
from app.workflow.graph import build_workflow_graph


class BrokenPlanner:
    name = "broken-live-planner"

    def __init__(self):
        self.calls = 0

    def propose(self, *args):
        self.calls += 1
        raise PlannerUnavailableError("timeout")


class FailingActionTools(DomainTools):
    def __init__(self):
        super().__init__()
        self.action_attempts = 0

    def execute_approved_action(self, action, idempotency_key):
        self.action_attempts += 1
        raise TransientToolError("simulated action outage")


class FailingGroundingTools(DomainTools):
    def call(self, *args, **kwargs):
        raise TransientToolError("credential=secret")


class WorkflowGraphTests(unittest.TestCase):
    def test_missing_evidence_resumes_same_run_and_clears_on_cycle_two(self):
        agent = KYCExceptionAgent()
        first = agent.run("KYC-1042", planner=HeuristicPlanner())
        self.assertEqual(first.pending_task.kind, PendingTaskKind.ACTION_APPROVAL)
        approved = agent.approve(first.pending_task.interrupt_key)
        self.assertEqual(approved.pending_task.kind, PendingTaskKind.DOCUMENT_SUBMISSION)
        finished = agent.resume(approved.pending_task.interrupt_key, {"documents": [{"type": "proof_of_address", "status": "verified"}]})
        self.assertEqual(finished.decision_id, first.decision_id)
        self.assertEqual(finished.cycle_count, 2)
        self.assertEqual(finished.outcome, Outcome.CLEAR)
        steps = [event.step for event in finished.trace]
        for name in ("load_customer", "verify_documents", "screen_watchlists", "load_risk"):
            self.assertEqual(steps.count(name), 2)

    def test_openrouter_exhaustion_preserves_sanctions_hard_stop(self):
        planner = BrokenPlanner()
        result = KYCExceptionAgent().run("KYC-1044", planner=planner)
        self.assertEqual(planner.calls, 3)
        self.assertEqual(result.outcome, Outcome.ESCALATE_COMPLIANCE)
        self.assertEqual(result.workflow_status, WorkflowStatus.BLOCKED)
        self.assertIsNone(result.pending_task)

    def test_openrouter_exhaustion_routes_other_cases_to_operations(self):
        result = KYCExceptionAgent().run("KYC-1045", planner=BrokenPlanner())
        self.assertEqual(result.pending_task.kind, PendingTaskKind.OPERATIONAL_REVIEW)
        self.assertEqual(result.workflow_status, WorkflowStatus.AWAITING_OPERATIONS)

    def test_resolved_interrupt_cannot_be_resumed_twice(self):
        agent = KYCExceptionAgent()
        first = agent.run("KYC-1042", planner=HeuristicPlanner())
        key = first.pending_task.interrupt_key
        agent.approve(key)
        with self.assertRaisesRegex(KeyError, "resolved"):
            agent.approve(key)

    def test_compiled_graph_exposes_the_interview_topology(self):
        graph = build_workflow_graph(DomainTools(), HeuristicPlanner())
        mermaid = graph.get_graph().draw_mermaid()
        for node in ("load_customer", "verify_documents", "screen_watchlists", "load_risk", "evidence_gate", "policy_precheck", "openrouter_reason", "reconcile_guard", "action_review", "await_documents", "operational_review", "increment_cycle"):
            self.assertIn(node, mermaid)

    def test_exhausted_action_retries_route_to_operations(self):
        tools = FailingActionTools()
        agent = KYCExceptionAgent(tools=tools)
        first = agent.run("KYC-1042", planner=HeuristicPlanner())
        result = agent.approve(first.pending_task.interrupt_key)
        self.assertEqual(tools.action_attempts, 3)
        self.assertEqual(result.pending_task.kind, PendingTaskKind.OPERATIONAL_REVIEW)

    def test_exhausted_grounding_retries_are_sanitized_and_handed_off(self):
        result = KYCExceptionAgent(tools=FailingGroundingTools()).run("KYC-1042", planner=HeuristicPlanner())
        self.assertEqual(result.workflow_status, WorkflowStatus.AWAITING_OPERATIONS)
        self.assertEqual(result.pending_task.kind, PendingTaskKind.OPERATIONAL_REVIEW)
        self.assertNotIn("secret", " ".join(event.detail for event in result.trace))

    def test_malformed_resume_keeps_interrupt_registered_and_current_node_is_labeled(self):
        agent = KYCExceptionAgent()
        first = agent.run("KYC-1042", planner=HeuristicPlanner())
        key = first.pending_task.interrupt_key
        self.assertEqual(first.current_node, "action_review")
        with self.assertRaises(ValueError):
            agent.resume(key, {"approved": "yes"})
        self.assertTrue(agent.has_pending(key))

    def test_invalid_then_valid_action_approval_does_not_poison_checkpoint(self):
        agent = KYCExceptionAgent()
        first = agent.run("KYC-1042", planner=HeuristicPlanner())
        key = first.pending_task.interrupt_key
        with self.assertRaisesRegex(ValueError, "boolean"):
            agent.resume(key, {"approved": "yes"})
        approved = agent.approve(key)
        self.assertEqual(approved.pending_task.kind, PendingTaskKind.DOCUMENT_SUBMISSION)
