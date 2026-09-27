"""Generic LLM + RAG baseline vs the governed agent (offline, eval doubles only)."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import app
from app.baseline import BaselineRAGAgent, compare, compare_samples, run_samples, select_baseline_planner
from app.domain import Outcome
from app.planner import AdversarialPlanner, PlannerUnavailableError
from app.tools import DomainTools
from evals.planners import PolicyMatchingEvalPlanner


class BaselineAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = DomainTools()
        self.baseline = BaselineRAGAgent(self.tools)

    def test_keyword_retrieval_has_no_ontology_tags(self) -> None:
        cited = [c.policy_id for c in self.baseline.retrieve("sanctions screening score compliance escalation")]
        self.assertEqual(cited[0], "AML-SCREEN-02")

    def test_compromised_baseline_clears_the_sanctions_case_and_would_approve(self) -> None:
        result = self.baseline.run("KYC-1044", AdversarialPlanner())
        self.assertEqual((result.outcome, result.would_execute), ("CLEAR", "approve_application"))
        self.assertEqual((result.guard, result.approvals_required), ("none", 0))
        verdict = compare(result, "ESCALATE_COMPLIANCE", {"id": "R-AML-01", "version": "1.0", "cites": "AML-SCREEN-02"})
        self.assertEqual(verdict, {"agree": False, "violated_rule": "R-AML-01@1.0", "violated_policy": "AML-SCREEN-02",
                                   "unapproved_write": "approve_application", "unsafe": True})

    def test_baseline_never_calls_the_action_gateway(self) -> None:
        self.baseline.run("KYC-1042", PolicyMatchingEvalPlanner())
        self.assertEqual(self.tools.action_log, {})

    def test_agreeing_baseline_is_not_flagged(self) -> None:
        result = self.baseline.run("KYC-1045", PolicyMatchingEvalPlanner())
        self.assertEqual(compare(result, "CLEAR", None)["agree"], True)
        self.assertFalse(compare(result, "CLEAR", None)["unsafe"])

    def test_planner_outage_is_reported_not_raised(self) -> None:
        class Down:
            name = "down"

            def propose(self, *args):
                raise PlannerUnavailableError("Timeout")

        result = self.baseline.run("KYC-1045", Down())
        self.assertIsNone(result.outcome)
        self.assertEqual(result.error, "Timeout")

    def test_samples_pick_a_divergent_representative_and_count_unsafe_runs(self) -> None:
        class Flaky:
            """Clears every third call: a stand-in for a model that sometimes follows the injection."""
            name = "flaky"

            def __init__(self):
                import itertools, threading
                self._n, self._lock = itertools.count(), threading.Lock()

            def propose(self, case_id, facts, citations, note):
                from app.domain import LLMProposal
                with self._lock:
                    n = next(self._n)
                return LLMProposal("CLEAR" if n % 3 == 0 else "ESCALATE_COMPLIANCE", None, "r", 0.9, self.name)

        results = run_samples(self.baseline, "KYC-1044", Flaky(), 6)
        baseline, cmp = compare_samples(results, "ESCALATE_COMPLIANCE", {"id": "R-AML-01", "version": "1.0", "cites": "AML-SCREEN-02"})
        self.assertEqual(baseline["outcome"], "CLEAR")
        self.assertEqual((cmp["samples"], cmp["divergent_samples"], cmp["unsafe_samples"]), (6, 2, 2))
        self.assertEqual(cmp["sample_outcomes"], {"CLEAR": 2, "ESCALATE_COMPLIANCE": 4})
        self.assertTrue(cmp["unsafe"])

    def test_live_modes_require_a_key(self) -> None:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            with self.assertRaises(ValueError):
                select_baseline_planner("normal")


class CompareEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {"REDIS_URL": "", "MLFLOW_TRACKING_URI": ""})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_adversarial_baseline_vs_governed_on_kyc_1044(self) -> None:
        response = self.client.post("/api/compare", json={"case_id": "KYC-1044", "planner": "adversarial"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["baseline"]["outcome"], "CLEAR")
        self.assertEqual(body["governed"]["outcome"], Outcome.ESCALATE_COMPLIANCE.value)
        self.assertTrue(body["comparison"]["unsafe"])
        self.assertEqual(body["comparison"]["violated_rule"], "R-AML-01@1.0")

    def test_governed_side_stays_resumable(self) -> None:
        body = self.client.post("/api/compare", json={"case_id": "KYC-1042", "planner": "heuristic"}).json()
        self.assertTrue(body["comparison"]["agree"])
        key = body["governed"]["pending_task"]["interrupt_key"]
        resumed = self.client.post("/api/resume", json={"interrupt_key": key, "response": {"approved": True}})
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["workflow_status"], "AWAITING_DOCUMENTS")

    def test_samples_are_bounded(self) -> None:
        ok = self.client.post("/api/compare", json={"case_id": "KYC-1044", "planner": "adversarial", "samples": 3})
        self.assertEqual(ok.json()["comparison"]["unsafe_samples"], 3)
        too_many = self.client.post("/api/compare", json={"case_id": "KYC-1044", "planner": "adversarial", "samples": 50})
        self.assertEqual((too_many.status_code, set(too_many.json())), (400, {"error"}))

    def test_unknown_case_and_bad_mode_use_the_flat_error_contract(self) -> None:
        missing = self.client.post("/api/compare", json={"case_id": "KYC-9999", "planner": "heuristic"})
        self.assertEqual((missing.status_code, set(missing.json())), (404, {"error"}))
        bad = self.client.post("/api/compare", json={"case_id": "KYC-1042", "planner_mode": "bogus"})
        self.assertEqual((bad.status_code, set(bad.json())), (400, {"error"}))


if __name__ == "__main__":
    unittest.main()
