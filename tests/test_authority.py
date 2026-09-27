"""Authority matrix: risk-tiered approvals, maker-checker, and role separation."""
import copy
import json
import unittest
from pathlib import Path

from app.agent import KYCExceptionAgent
from app.authority import AuthorityError, check_approval, requirement, satisfied
from app.ontology import Ontology, OntologyError, load_ontology
from evals.planners import PolicyMatchingEvalPlanner

RAW = json.loads((Path(__file__).resolve().parents[1] / "data" / "ontology.json").read_text())


class MatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authority = load_ontology().authority

    def test_risk_tiers(self) -> None:
        self.assertEqual(requirement(self.authority, "request_document", "MEDIUM").approvals, 1)
        self.assertEqual(requirement(self.authority, "request_document", "HIGH").approvals, 2)
        self.assertEqual(requirement(self.authority, "open_manual_review", "HIGH").approvals, 2)
        critical = requirement(self.authority, "open_manual_review", "CRITICAL")
        self.assertEqual((critical.roles, critical.distinct_roles), (("kyc_lead", "compliance"), True))
        promote = requirement(self.authority, "promote_amendment")
        self.assertEqual((promote.approvals, promote.distinct_roles), (2, True))

    def test_maker_checker_and_roles(self) -> None:
        req = requirement(self.authority, "open_manual_review", "HIGH")
        first = check_approval(req, [], {"approver": "an", "role": "analyst"})
        self.assertFalse(satisfied(req, [first]))
        with self.assertRaisesRegex(AuthorityError, "maker-checker"):
            check_approval(req, [first], {"approver": "an", "role": "kyc_lead"})
        with self.assertRaisesRegex(AuthorityError, "role"):
            check_approval(req, [first], {"approver": "x", "role": "intern"})
        second = check_approval(req, [first], {"approver": "lan", "role": "analyst"})
        self.assertTrue(satisfied(req, [first, second]))

    def test_distinct_roles(self) -> None:
        req = requirement(self.authority, "promote_amendment")
        lead = check_approval(req, [], {"approver": "lan", "role": "kyc_lead"})
        with self.assertRaisesRegex(AuthorityError, "different role"):
            check_approval(req, [lead], {"approver": "minh", "role": "kyc_lead"})

    def test_loader_rejects_an_incomplete_or_impossible_matrix(self) -> None:
        missing = copy.deepcopy(RAW)
        del missing["authority"]["actions"]["open_manual_review"]
        with self.assertRaisesRegex(OntologyError, "open_manual_review"):
            Ontology.from_dict(missing)
        impossible = copy.deepcopy(RAW)
        impossible["authority"]["actions"]["promote_amendment"]["approvals"] = 3
        with self.assertRaisesRegex(OntologyError, "distinct roles"):
            Ontology.from_dict(impossible)
        unknown = copy.deepcopy(RAW)
        unknown["authority"]["actions"]["request_document"]["roles"] = ["ceo"]
        with self.assertRaisesRegex(OntologyError, "subset"):
            Ontology.from_dict(unknown)


class WorkflowAuthorityTests(unittest.TestCase):
    def run_1046(self):
        agent = KYCExceptionAgent()
        return agent, agent.run("KYC-1046", planner=PolicyMatchingEvalPlanner())

    def test_same_person_cannot_approve_twice_and_the_task_stays_pending(self) -> None:
        agent, first = self.run_1046()
        half = agent.approve(first.pending_task.interrupt_key, approver="an", role="analyst")
        with self.assertRaisesRegex(ValueError, "maker-checker"):
            agent.approve(half.pending_task.interrupt_key, approver="an", role="analyst")
        self.assertTrue(agent.has_pending(half.pending_task.interrupt_key))
        done = agent.approve(half.pending_task.interrupt_key, approver="lan", role="kyc_lead")
        self.assertEqual(done.governance["approvals"], 2)
        self.assertIn("Approved 2/2", next(e for e in done.trace if e.step == "action_review").detail)

    def test_disallowed_role_is_refused_before_the_graph(self) -> None:
        agent, first = self.run_1046()
        with self.assertRaisesRegex(ValueError, "role"):
            agent.approve(first.pending_task.interrupt_key, approver="x", role="intern")

    def test_a_rejection_after_one_approval_ends_the_review(self) -> None:
        agent, first = self.run_1046()
        half = agent.approve(first.pending_task.interrupt_key, approver="an", role="analyst")
        rejected = agent.reject(half.pending_task.interrupt_key, "evidence pack incomplete")
        self.assertEqual(rejected.workflow_status.value, "REJECTED")
        self.assertIsNone(rejected.executed_action)

    def test_medium_risk_document_request_needs_one_approval(self) -> None:
        agent = KYCExceptionAgent()
        first = agent.run("KYC-1042", planner=PolicyMatchingEvalPlanner())
        self.assertEqual(first.pending_task.payload["authority"]["approvals"], 1)
        approved = agent.approve(first.pending_task.interrupt_key)
        self.assertEqual(approved.executed_action["approved_by"], [{"approver": "reviewer", "role": "analyst"}])


if __name__ == "__main__":
    unittest.main()
