import unittest

from app import i18n
from app.domain import AgentDecision, LLMProposal, PendingTask, PendingTaskKind, TraceEvent, WorkflowStatus, Outcome


class WorkflowContractTests(unittest.TestCase):
    def test_pending_task_serializes_as_plain_json_data(self) -> None:
        task = PendingTask(
            kind=PendingTaskKind.DOCUMENT_SUBMISSION,
            interrupt_key="interrupt-1",
            title="Documents required",
            message="Upload proof of address",
            payload={"documents": ["proof_of_address"]},
            allowed_responses=["submit"],
        )
        self.assertEqual(task.to_dict()["kind"], "document_submission")

    def test_every_safe_failure_reason_renders_in_both_languages(self) -> None:
        for key in ("ai_unavailable", "tool_unavailable", "policy_unavailable", "cycle_exhausted", "submission_attempts_exhausted"):
            self.assertTrue(i18n.render_reason(key, {}, "en"))
            self.assertTrue(i18n.render_reason(key, {}, "vi"))

    def test_status_enums_are_stable_api_values(self) -> None:
        self.assertEqual(WorkflowStatus.AWAITING_DOCUMENTS.value, "AWAITING_DOCUMENTS")

    def test_optional_planner_and_workflow_fields_serialize(self) -> None:
        decision = AgentDecision(
            case_id="case", decision_id="decision", outcome=Outcome.CLEAR,
            outcome_label="CLEAR", summary="ok", proposal_confidence=None,
            risk_level="UNKNOWN", risk_label="UNKNOWN", facts=[], citations=[],
            tool_calls=[], trace=[TraceEvent("x", "X", "detail")], model=None,
            llm_rationale=None,
        )
        value = decision.to_dict()
        self.assertEqual(value["workflow_status"], "COMPLETED")
        self.assertIsNone(value["pending_task"])
        self.assertEqual(value["trace"][0]["cycle"], 1)

    def test_render_facts_skips_unavailable_groups(self) -> None:
        lines = i18n.render_facts({"verify_documents": None, "screen_sanctions": None,
                                   "get_risk_profile": {"level": "UNKNOWN", "score": None}}, "en")
        self.assertEqual(lines, ["Risk tier: UNKNOWN (score None)"])

    def test_llm_proposal_usage_defaults_to_empty(self) -> None:
        self.assertEqual(LLMProposal("CLEAR", None, "ok", 1.0, "model").usage, {})

    def test_workflow_ui_strings_are_available_in_both_languages(self) -> None:
        for key in ("workflow_status", "cycle", "pending_document_submission",
                    "operational_handoff", "planner_attempts", "tokens", "cost"):
            self.assertTrue(i18n.ui_text("en", key))
            self.assertTrue(i18n.ui_text("vi", key))
