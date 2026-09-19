import unittest
from unittest.mock import patch

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

    def test_reconcile_guard_preserves_sanctions_hard_stop_on_planner_failure(self) -> None:
        state = {
            **self.state,
            "planner_status": "failed",
            "policy_verdict": {
                "outcome": "ESCALATE_COMPLIANCE", "action": None,
                "risk_level": "CRITICAL", "reason_key": "sanctions_hit",
                "reason_params": {"score": 0.91},
            },
        }
        update = self.nodes.reconcile_guard(state)
        self.assertEqual(update["decision"]["outcome"], "ESCALATE_COMPLIANCE")
        self.assertIsNone(update["action_payload"])
        self.assertEqual(update["final_outcome"], "BLOCKED")

    def test_reconcile_guard_degrades_to_manual_review_on_planner_failure(self) -> None:
        state = {
            **self.state,
            "planner_status": "failed",
            "policy_verdict": {
                "outcome": "REQUEST_EVIDENCE", "action": "request_document",
                "risk_level": "MEDIUM", "reason_key": "missing_evidence",
                "reason_params": {"fields": ["proof_of_address"]},
            },
        }
        update = self.nodes.reconcile_guard(state)
        self.assertEqual(update["decision"]["outcome"], "MANUAL_REVIEW")
        self.assertEqual(update["decision"]["reason_key"], "ai_unavailable")
        self.assertIsNone(update["action_payload"])
        self.assertEqual(update["operational_reason"], "ai_unavailable")
        self.assertNotIn("final_outcome", update)


class WorkflowHumanNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nodes = WorkflowNodes(DomainTools(), HeuristicPlanner())
        self.state = {
            **initial_state("KYC-1042", "run-1", "en", "normal"),
            "decision": {
                "outcome": "REQUEST_EVIDENCE",
                "action": "request_document",
                "action_payload": {
                    "case_id": "KYC-1042",
                    "action": "request_document",
                    "documents": ["proof_of_address"],
                    "policy_versions": ["KYC-EVIDENCE-07:2026.3"],
                },
            },
        }

    @patch("app.workflow.nodes.interrupt", return_value={"approved": False, "reason": "Need a newer document"})
    def test_rejection_records_reason_without_executing(self, mocked_interrupt) -> None:
        update = self.nodes.action_review(self.state)
        self.assertEqual(update["review_result"], {"approved": False, "reason": "Need a newer document"})
        self.assertIsNone(update.get("action_result"))
        mocked_interrupt.assert_called_once_with({
            "kind": "action_approval",
            "case_id": "KYC-1042",
            "run_id": "run-1",
            "message_key": "approve_prompt",
            "action_payload": self.state["decision"]["action_payload"],
            "allowed_responses": ["approve", "reject"],
        })

    @patch("app.workflow.nodes.interrupt", return_value={"approved": True})
    def test_approval_keeps_exact_payload_for_execution(self, mocked_interrupt) -> None:
        update = self.nodes.action_review(self.state)
        self.assertEqual(update["review_result"], {"approved": True})
        self.assertEqual(update["action_payload"], self.state["decision"]["action_payload"])

    @patch("app.workflow.nodes.interrupt", return_value={"approved": False, "reason": "   "})
    def test_rejection_requires_nonblank_reason(self, mocked_interrupt) -> None:
        with self.assertRaises(ValueError):
            self.nodes.action_review(self.state)

    def test_execute_action_requires_approved_review_and_uses_payload_key(self) -> None:
        with self.assertRaises(PermissionError):
            self.nodes.execute_action(self.state)
        with patch.object(self.nodes.tools, "execute_approved_action", return_value={"status": "executed"}) as execute:
            state = {**self.state, "review_result": {"approved": True}}
            update = self.nodes.execute_action(state)
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[0], self.state["decision"]["action_payload"])
        self.assertEqual(update["action_result"], {"status": "executed"})

    @patch("app.workflow.nodes.interrupt", return_value={
        "documents": [{"type": "proof_of_address", "status": "verified"}]
    })
    def test_document_submission_is_validated_before_incrementing_cycle(self, mocked_interrupt) -> None:
        resumed = self.nodes.await_documents(self.state)
        validated = self.nodes.validate_submission({**self.state, **resumed})
        self.assertTrue(validated["submission_valid"])
        incremented = self.nodes.increment_cycle({**self.state, **resumed, **validated})
        self.assertEqual(incremented["cycle_count"], 2)
        self.assertEqual(validated["submitted_documents"], [{"type": "proof_of_address", "status": "verified"}])

    @patch("app.workflow.nodes.interrupt", return_value={"documents": []})
    def test_invalid_submission_preserves_existing_documents(self, mocked_interrupt) -> None:
        state = {**self.state, "submitted_documents": [{"type": "passport", "status": "verified"}]}
        resumed = self.nodes.await_documents(state)
        validated = self.nodes.validate_submission({**state, **resumed})
        self.assertFalse(validated["submission_valid"])
        self.assertNotIn("submitted_documents", validated)

    def test_increment_cycle_clears_cycle_fields_but_not_append_only_history(self) -> None:
        state = {
            **self.state,
            "cycle_count": 1,
            "customer_facts": {"x": 1}, "document_facts": {"x": 1},
            "screening_facts": {"x": 1}, "risk_facts": {"x": 1},
            "facts": {"x": 1}, "citations": [{"policy_id": "p"}],
            "policy_verdict": {"outcome": "REQUEST_EVIDENCE"},
            "proposal": {"outcome": "REQUEST_EVIDENCE"}, "decision": {"action": "request_document"},
            "review_result": {"approved": True}, "action_result": {"status": "executed"},
            "trace": [{"step": "old"}], "tool_calls": [{"name": "old"}], "tool_errors": [{"node": "old"}],
            "submitted_documents": [{"type": "proof_of_address", "status": "verified"}],
        }
        update = self.nodes.increment_cycle(state)
        self.assertEqual(update["cycle_count"], 2)
        for field in ("customer_facts", "document_facts", "screening_facts", "risk_facts", "facts", "citations", "policy_verdict", "proposal", "decision", "review_result", "action_result", "evidence_ok", "final_outcome"):
            self.assertIsNone(update[field])
        self.assertEqual(update["submission_attempts"], 0)
        self.assertFalse(update["submission_exhausted"])
        self.assertNotIn("trace", update)
        self.assertNotIn("tool_calls", update)
        self.assertNotIn("tool_errors", update)
        self.assertNotIn("submitted_documents", update)

    def test_validate_submission_exhausts_after_max_attempts(self) -> None:
        state = {**self.state, "submission_attempts": 2, "document_submission": {"documents": []}}
        validated = self.nodes.validate_submission(state)
        self.assertFalse(validated["submission_valid"])
        self.assertTrue(validated["submission_exhausted"])
        self.assertEqual(validated["submission_attempts"], 3)
        self.assertEqual(validated["operational_reason"], "submission_attempts_exhausted")

    @patch("app.workflow.nodes.interrupt", return_value={"acknowledged": True})
    def test_operational_review_creates_safe_manual_handoff(self, mocked_interrupt) -> None:
        state = {**initial_state("KYC-1042", "run-1", "en", "normal"), "operational_reason": "tool_unavailable"}
        update = self.nodes.operational_review(state)
        self.assertEqual(update["decision"]["outcome"], "MANUAL_REVIEW")
        self.assertEqual(update["decision"]["risk_level"], "UNKNOWN")
        self.assertEqual(update["action_payload"], None)
        self.assertEqual(update["review_result"], {"acknowledged": True})

    def test_finalize_reads_final_outcome_and_defaults_to_completed(self) -> None:
        for final_outcome, expected in ((None, "COMPLETED"), ("BLOCKED", "BLOCKED"), ("REJECTED", "REJECTED")):
            state = {**self.state, "final_outcome": final_outcome} if final_outcome else self.state
            update = self.nodes.finalize(state)
            self.assertEqual(update["workflow_status"], expected)
            self.assertEqual(len(update["trace"]), 1)
            self.assertEqual(update["trace"][0]["step"], "finalize")

    def test_action_error_handler_is_sanitized_and_degraded(self) -> None:
        update = self.nodes.action_error_handler(self.state, NodeError(RuntimeError("credential=secret"), "execute_action"))
        self.assertEqual(update["action_result"], {"status": "failed", "category": "RuntimeError"})
        self.assertEqual(update["operational_reason"], "tool_unavailable")
        self.assertNotIn("credential=secret", repr(update))


if __name__ == "__main__":
    unittest.main()
