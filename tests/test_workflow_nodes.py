import unittest

from app.domain import LLMProposal
from app.planner import HeuristicPlanner
from app.tools import DomainTools
from app.workflow.nodes import NodeError, WorkflowNodes
from app.workflow.state import initial_state


class WorkflowNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nodes = WorkflowNodes(DomainTools(), HeuristicPlanner())
        self.state = initial_state("KYC-1042", "run-1", "en", "normal")

    def test_parallel_read_nodes_write_disjoint_fact_keys(self) -> None:
        updates = [
            self.nodes.load_customer(self.state),
            self.nodes.verify_documents(self.state),
            self.nodes.screen_watchlists(self.state),
            self.nodes.load_risk(self.state),
        ]
        owned = [{key for key in update if key.endswith("_facts")} for update in updates]
        self.assertEqual(owned, [
            {"customer_facts"}, {"document_facts"}, {"screening_facts"}, {"risk_facts"}
        ])

    def test_evidence_gate_rebuilds_the_policy_input_shape(self) -> None:
        state = dict(self.state)
        for node in (
            self.nodes.load_customer,
            self.nodes.verify_documents,
            self.nodes.screen_watchlists,
            self.nodes.load_risk,
        ):
            state.update(node(state))
        update = self.nodes.evidence_gate(state)
        self.assertEqual(set(update["facts"]), {
            "get_case", "verify_documents", "screen_sanctions", "get_risk_profile"
        })

    def test_policy_precheck_runs_without_a_model_proposal(self) -> None:
        facts = {
            "get_case": {},
            "verify_documents": {"name_match": True, "liveness_passed": True, "missing_fields": []},
            "screen_sanctions": {"match_score": 0.91},
            "get_risk_profile": {"level": "HIGH"},
        }
        update = self.nodes.policy_precheck({**self.state, "facts": facts, "citations": []})
        self.assertEqual(update["policy_verdict"]["outcome"], "ESCALATE_COMPLIANCE")

    def test_intake_validates_mode_and_initializes_counters(self) -> None:
        state = {"case_id": "KYC-1042", "lang": "en", "planner_mode": "normal"}
        update = self.nodes.intake(state)
        self.assertEqual(update["workflow_status"], "RUNNING")
        self.assertEqual((update["cycle_count"], update["max_cycles"]), (1, 2))
        with self.assertRaises(ValueError):
            self.nodes.intake({**state, "planner_mode": "heuristic"})

    def test_planner_error_handler_is_sanitized(self) -> None:
        update = self.nodes.planner_error_handler(
            self.state, NodeError(RuntimeError("secret payload"), "openrouter_reason")
        )
        self.assertEqual(update["planner_status"], "failed")
        self.assertNotIn("secret payload", repr(update))

    def test_tool_error_handler_is_sanitized_and_marks_fact_unavailable(self) -> None:
        update = self.nodes.tool_error_handler("risk_facts")(
            self.state, NodeError(RuntimeError("credential=secret"), "load_risk")
        )
        self.assertIsNone(update["risk_facts"])
        self.assertEqual(update["tool_errors"], [{"node": "load_risk", "category": "RuntimeError"}])
        self.assertNotIn("credential=secret", repr(update))

    def test_reconciliation_keeps_override_metadata(self) -> None:
        state = {
            **self.state,
            "policy_verdict": {
                "outcome": "ESCALATE_COMPLIANCE", "action": None,
                "risk_level": "CRITICAL", "reason_key": "sanctions_hit",
                "reason_params": {"score": 0.91, "threshold": 0.8},
            },
            "proposal": {
                "outcome": "CLEAR", "action": None, "rationale": "unsafe",
                "confidence": 0.9, "model": "test-model",
            },
            "citations": [{"policy_id": "AML-SCREEN-02", "version": "2026.4", "section": "2.3", "excerpt": "..."}],
        }
        decision = self.nodes.reconcile_guard(state)["decision"]
        self.assertEqual(decision["outcome"], "ESCALATE_COMPLIANCE")
        self.assertEqual(decision["guardrail_override"]["model"], "test-model")
        self.assertEqual(decision["guardrail_override"]["final_outcome"], "ESCALATE_COMPLIANCE")

    def test_safe_failure_preserves_sanctions_hard_stop(self) -> None:
        state = {
            **self.state,
            "policy_verdict": {
                "outcome": "ESCALATE_COMPLIANCE", "action": None,
                "risk_level": "CRITICAL", "reason_key": "sanctions_hit",
                "reason_params": {"score": 0.91},
            },
        }
        update = self.nodes.safe_failure(state)
        self.assertEqual(update["decision"]["outcome"], "ESCALATE_COMPLIANCE")
        self.assertIsNone(update["action_payload"])


if __name__ == "__main__":
    unittest.main()
