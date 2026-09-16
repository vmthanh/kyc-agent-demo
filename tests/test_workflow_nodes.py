import unittest

from app.planner import HeuristicPlanner
from app.tools import DomainTools
from app.workflow.nodes import WorkflowNodes
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


if __name__ == "__main__":
    unittest.main()
