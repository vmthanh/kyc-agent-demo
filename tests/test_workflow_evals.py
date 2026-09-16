import unittest
from unittest.mock import patch

from app.agent import KYCExceptionAgent
from app.domain import Outcome, PendingTaskKind, WorkflowStatus
from app.planner import PlannerUnavailableError
from app.tools import DomainTools, action_idempotency_key
from evals.planners import (
    CompromisedEvalPlanner,
    PolicyMatchingEvalPlanner,
    UnavailableEvalPlanner,
)


class WorkflowEvalDoubleTests(unittest.TestCase):
    def test_smoke_strips_placeholder_key_before_gating(self) -> None:
        from evals.openrouter_smoke import main

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "  your_key_here  "}, clear=False):
            self.assertEqual(main(["--case", "KYC-1045"]), 2)

    def test_policy_matching_eval_planner_matches_deterministic_policy(self) -> None:
        proposal = PolicyMatchingEvalPlanner().propose(
            "KYC-1045",
            {
                "verify_documents": {"name_match": True, "liveness_passed": True, "missing_fields": []},
                "screen_sanctions": {"match_score": 0.02},
                "get_risk_profile": {"level": "LOW"},
            },
            [],
            "",
        )
        self.assertEqual(proposal.outcome, "CLEAR")
        self.assertIsNone(proposal.action)

    def test_compromised_eval_planner_is_explicitly_unsafe(self) -> None:
        proposal = CompromisedEvalPlanner().propose("KYC-1044", {}, [], "ignore policy")
        self.assertEqual(proposal.outcome, "CLEAR")
        self.assertIsNone(proposal.action)

    def test_unavailable_eval_planner_raises_retryable_error(self) -> None:
        with self.assertRaisesRegex(PlannerUnavailableError, "unavailable"):
            UnavailableEvalPlanner().propose("KYC-1045", {}, [], "")

    def test_eval_doubles_are_not_runtime_planner_choices(self) -> None:
        from app.planner import select_planner
        from unittest.mock import patch

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "", "KYC_AGENT_PLANNER": ""}, clear=False):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                select_planner("normal")

    def test_request_evidence_trajectory_resumes_and_clears(self) -> None:
        agent = KYCExceptionAgent()
        first = agent.run("KYC-1042", planner=PolicyMatchingEvalPlanner())
        self.assertEqual(first.outcome, Outcome.REQUEST_EVIDENCE)
        self.assertEqual(first.pending_task.kind, PendingTaskKind.ACTION_APPROVAL)
        approved = agent.approve(first.pending_task.interrupt_key)
        self.assertEqual(approved.pending_task.kind, PendingTaskKind.DOCUMENT_SUBMISSION)
        finished = agent.resume(
            approved.pending_task.interrupt_key,
            {"documents": [{"type": "proof_of_address", "status": "verified"}]},
        )
        self.assertEqual(finished.outcome, Outcome.CLEAR)
        self.assertEqual(finished.cycle_count, 2)
        self.assertIsNone(finished.pending_task)

    def test_manual_review_trajectory_executes_approved_action(self) -> None:
        agent = KYCExceptionAgent()
        first = agent.run("KYC-1043", planner=PolicyMatchingEvalPlanner())
        self.assertEqual(first.outcome, Outcome.MANUAL_REVIEW)
        self.assertEqual(first.pending_task.kind, PendingTaskKind.ACTION_APPROVAL)
        finished = agent.approve(first.pending_task.interrupt_key)
        self.assertEqual(finished.outcome, Outcome.MANUAL_REVIEW)
        self.assertEqual(finished.executed_action["status"], "executed")
        self.assertIsNone(finished.pending_task)

    def test_compromised_and_unavailable_sanctions_paths_are_strict(self) -> None:
        compromised = KYCExceptionAgent().run("KYC-1044", planner=CompromisedEvalPlanner())
        self.assertEqual(compromised.outcome, Outcome.ESCALATE_COMPLIANCE)
        self.assertIsNotNone(compromised.guardrail_override)
        self.assertIsNone(compromised.pending_task)

        unavailable = KYCExceptionAgent().run("KYC-1044", planner=UnavailableEvalPlanner())
        self.assertEqual(unavailable.outcome, Outcome.ESCALATE_COMPLIANCE)
        self.assertEqual(unavailable.workflow_status, WorkflowStatus.BLOCKED)
        self.assertIsNone(unavailable.pending_task)

    def test_clear_and_unavailable_paths_are_distinct(self) -> None:
        clear = KYCExceptionAgent().run("KYC-1045", planner=PolicyMatchingEvalPlanner())
        self.assertEqual(clear.outcome, Outcome.CLEAR)
        self.assertIsNone(clear.pending_task)

        unavailable = KYCExceptionAgent().run("KYC-1045", planner=UnavailableEvalPlanner())
        self.assertEqual(unavailable.workflow_status, WorkflowStatus.AWAITING_OPERATIONS)
        self.assertEqual(unavailable.outcome, Outcome.MANUAL_REVIEW)
        self.assertEqual(unavailable.pending_task.kind, PendingTaskKind.OPERATIONAL_REVIEW)

    def test_duplicate_gateway_request_replays_one_ticket(self) -> None:
        tools = DomainTools()
        payload = {"case_id": "KYC-1043", "action": "open_manual_review"}
        key = action_idempotency_key(payload)
        first = tools.execute_approved_action(payload, key)
        replay = tools.execute_approved_action(payload, key)
        self.assertEqual(first["ticket_id"], replay["ticket_id"])
        self.assertTrue(replay["replayed"])


if __name__ == "__main__":
    unittest.main()
