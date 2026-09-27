"""Reflect: reviewer signal -> candidate amendment -> replay -> gated promotion."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import ontology as ontology_module
from app.agent import KYCExceptionAgent
from app.api import app
from app.ontology import load_ontology, reset_active
from app.reflect import NarrowingDrafter, ReflectStore, ReviewSignal, replay
from evals.planners import PolicyMatchingEvalPlanner

REASON = "Tamper 0.09 is borderline; borderline captures need manual review"


def rejected_kyc_1043(store: ReflectStore):
    agent = KYCExceptionAgent(reflect=store)
    first = agent.run("KYC-1043", planner=PolicyMatchingEvalPlanner())
    return agent, agent.reject(first.pending_task.interrupt_key, REASON)


class ReflectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_active()
        self.addCleanup(reset_active)
        self.tmp = Path(tempfile.mkdtemp())
        self.store = ReflectStore(self.tmp)

    def test_rejection_becomes_a_signal_once(self) -> None:
        agent, decision = rejected_kyc_1043(self.store)
        self.assertIn("reviewer signal captured", decision.trace[-1].detail)
        self.assertEqual(decision.trace[-1].phase, "REFLECT")
        (signal,) = self.store.signals.values()
        self.assertEqual((signal.kind, signal.rule_id, signal.reviewer_reason), ("rejection", "R-ID-EXP-01", REASON))
        agent.relocalize(decision.decision_id, "vi")
        self.assertEqual(len(self.store.signals), 1)

    def test_narrowing_amendment_replays_cleanly_and_promotes_after_approval(self) -> None:
        rejected_kyc_1043(self.store)
        amendment = self.store.propose(signal_id="SIG-001")
        self.assertEqual(amendment.status, "proposed")
        self.assertIn({"path": "params.max_tamper", "before": 0.1, "after": 0.09}, amendment.diff)
        self.assertEqual(amendment.impact["safety_regressions"], [])
        self.assertEqual([c["case_id"] for c in amendment.impact["changed"]], ["KYC-1043"])
        self.assertEqual(load_ontology().rule("R-ID-EXP-01").version, "1.0")  # nothing activates on proposal

        with self.assertRaisesRegex(ValueError, "role"):
            self.store.approve(amendment.id, "an.nguyen", "analyst")
        self.store.approve(amendment.id, "lan.pham", "kyc_lead")
        self.assertEqual(amendment.status, "promoted")
        self.assertEqual(load_ontology().rule("R-ID-EXP-01").ref, "R-ID-EXP-01@1.1")
        self.assertTrue((self.tmp / "ontology-v2.1.json").exists())
        self.assertIn("ontology_promoted", (self.tmp / "audit.jsonl").read_text())
        self.assertEqual(ontology_module.DEFAULT_PATH.read_text().count('"max_tamper": 0.10'), 1)  # shipped file untouched

        after = KYCExceptionAgent(reflect=ReflectStore(self.tmp)).run("KYC-1043", planner=PolicyMatchingEvalPlanner())
        self.assertEqual((after.outcome.value, after.rule["id"]), ("MANUAL_REVIEW", "R-ID-01"))

    def test_unsafe_amendment_is_blocked_by_replay(self) -> None:
        aml = copy.deepcopy(next(r for r in load_ontology().raw["cognitive"]["rules"] if r["id"] == "R-AML-01"))
        aml["params"]["threshold"] = 0.95
        amendment = self.store.propose(rule=aml)
        self.assertEqual(amendment.status, "blocked")
        self.assertGreater(len(amendment.impact["safety_regressions"]), 0)
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.store.approve(amendment.id, "lan.pham", "compliance")
        self.assertEqual(load_ontology().rule("R-AML-01").params["threshold"], 0.80)

    def test_invalid_candidate_is_rejected_by_the_loader(self) -> None:
        rule = copy.deepcopy(next(r for r in load_ontology().raw["cognitive"]["rules"] if r["id"] == "R-ID-01"))
        rule["when"] = {"all": [{"fact": "case_note", "op": "not_empty"}]}
        amendment = self.store.propose(rule=rule)
        self.assertEqual(amendment.status, "invalid")
        self.assertIn("case_note", amendment.errors[0])

    def test_hard_stop_rules_cannot_be_narrowed_from_a_signal(self) -> None:
        signal = ReviewSignal("SIG-X", "rejection", "KYC-1044", "r", "R-AML-01", "1.0", "ESCALATE_COMPLIANCE", None, "x", {})
        rule = next(r for r in load_ontology().raw["cognitive"]["rules"] if r["id"] == "R-AML-01")
        with self.assertRaisesRegex(Exception, "hard-stop"):
            NarrowingDrafter().draft(rule, signal)

    def test_rejected_amendment_never_activates(self) -> None:
        rejected_kyc_1043(self.store)
        amendment = self.store.propose(signal_id="SIG-001")
        self.store.approve(amendment.id, "lan.pham", "compliance", approved=False, reason="keep 0.10")
        self.assertEqual(amendment.status, "rejected")
        self.assertEqual(load_ontology().version, "2.0")

    def test_identical_ontologies_replay_with_no_change(self) -> None:
        onto = load_ontology()
        impact = replay(onto, onto)
        self.assertEqual((impact["changed"], impact["agreement_before"]), ([], 1.0))


class ReflectAPITests(unittest.TestCase):
    def setUp(self) -> None:
        reset_active()
        self.addCleanup(reset_active)
        env = patch.dict(os.environ, {"REDIS_URL": "", "MLFLOW_TRACKING_URI": ""})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        app.state.agent.reflect = ReflectStore(Path(tempfile.mkdtemp()))

    def test_end_to_end_over_http(self) -> None:
        run = self.client.post("/api/run", json={"case_id": "KYC-1043", "planner": "heuristic"}).json()
        self.client.post("/api/reject", json={"approval_key": run["pending_task"]["interrupt_key"], "reason": REASON})
        (signal,) = self.client.get("/api/reflect/signals").json()
        amendment = self.client.post("/api/amendments", json={"signal_id": signal["id"]}).json()
        self.assertEqual(amendment["status"], "proposed")
        reviewed = self.client.post(f"/api/amendments/{amendment['id']}/review",
                                    json={"approver": "lan.pham", "role": "kyc_lead"}).json()
        self.assertEqual(reviewed["status"], "promoted")
        onto = self.client.get("/api/ontology").json()
        self.assertEqual((onto["version"], onto["promoted"]), ("2.1", True))
        self.assertEqual(self.client.post("/api/ontology/reset").json()["version"], "2.0")

    def test_raw_rule_and_error_contract(self) -> None:
        self.assertEqual(self.client.get("/api/ontology/rules/R-AML-01").json()["params"]["threshold"], 0.8)
        self.assertEqual(self.client.get("/api/ontology/rules/NOPE").status_code, 404)
        both = self.client.post("/api/amendments", json={})
        self.assertEqual((both.status_code, set(both.json())), (400, {"error"}))
        missing = self.client.post("/api/amendments/AMD-nope/review", json={"approver": "a", "role": "kyc_lead"})
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
