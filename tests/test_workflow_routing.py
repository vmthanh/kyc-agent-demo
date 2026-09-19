import unittest

from app.workflow.routing import (
    route_after_action,
    route_after_evidence_gate,
    route_after_guard,
    route_after_policy_precheck,
)
from app.workflow.state import initial_state


class WorkflowRoutingTests(unittest.TestCase):
    def test_initial_state_sets_one_based_bounded_cycle(self) -> None:
        state = initial_state("KYC-1042", "run-1", "en", "normal")
        self.assertEqual((state["cycle_count"], state["max_cycles"]), (1, 2))
        self.assertEqual(state["trace"], [])
        self.assertEqual(state["tool_errors"], [])

    def test_evidence_error_routes_to_operational_review(self) -> None:
        self.assertEqual(route_after_evidence_gate({"evidence_ok": False}), "insufficient")

    def test_missing_policy_stops_before_the_model(self) -> None:
        self.assertEqual(route_after_policy_precheck({"policy_status": "failed"}), "bail")

    def test_planner_failure_preserves_a_sanctions_hard_stop(self) -> None:
        state = {"decision": {"outcome": "ESCALATE_COMPLIANCE"}, "planner_status": "failed"}
        self.assertEqual(route_after_guard(state), "blocked")

    def test_planner_failure_otherwise_escalates_to_operations(self) -> None:
        state = {"decision": {"outcome": "MANUAL_REVIEW"}, "planner_status": "failed"}
        self.assertEqual(route_after_guard(state), "escalate")

    def test_evidence_route_stops_after_second_evaluation(self) -> None:
        state = {"decision": {"outcome": "REQUEST_EVIDENCE"}, "cycle_count": 2, "max_cycles": 2}
        self.assertEqual(route_after_guard(state), "escalate")

    def test_successful_document_request_waits_for_documents(self) -> None:
        state = {"action_result": {"status": "executed", "action": "request_document"}}
        self.assertEqual(route_after_action(state), "docs_requested")
