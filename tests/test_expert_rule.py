"""Captured expert judgment: name-difference measurement and the bounded R-ID-EXP-01 rule."""
import copy
import itertools
import json
import unittest
from pathlib import Path

from app import i18n
from app.domain import Outcome
from app.names import fold_diacritics, name_diff, ocr_canonical
from app.ontology import FactMissingError, Ontology, OntologyError
from app.policy import evaluate
from app.tools import DomainTools

ROOT = Path(__file__).resolve().parents[1]
RAW = json.loads((ROOT / "data" / "ontology.json").read_text())


def docs(name_match=False, liveness=True, tamper=0.02, missing=(), diff=None):
    d = {"name_match": name_match, "liveness_passed": liveness, "tamper_score": tamper, "missing_fields": list(missing)}
    if diff is not None:
        d["name_diff"] = diff
    return d


def facts(verify_documents, score=0.03):
    return {
        "get_case": {},
        "verify_documents": verify_documents,
        "screen_sanctions": {"match_score": score, "candidate": None},
        "get_risk_profile": {"level": "MEDIUM", "score": 0.5},
    }


OCR = name_diff("Trần Minh Anh", "Tran Mlnh Anh")
REAL = name_diff("Nguyễn Văn Bình", "Phạm Thị Lan")


class NameDiffTests(unittest.TestCase):
    def test_fold_and_canonicalise(self) -> None:
        self.assertEqual(fold_diacritics("Trần Đức Hưng"), "tran duc hung")
        self.assertEqual(ocr_canonical("mlnh"), "minh")
        self.assertEqual(ocr_canonical("rnai"), "mai")

    def test_identical_names(self) -> None:
        d = name_diff("Le Thi Hoa", "Le Thi Hoa")
        self.assertEqual((d["tokens_differing"], d["diacritic_only"], d["ocr_explainable"]), (0, False, False))

    def test_diacritic_only_is_explainable(self) -> None:
        d = name_diff("Trần Minh Anh", "Tran Minh Anh")
        self.assertTrue(d["diacritic_only"])
        self.assertTrue(d["ocr_explainable"])
        self.assertEqual((d["declared_token"], d["document_token"]), ("Trần", "Tran"))

    def test_single_glyph_confusion_with_lost_diacritics(self) -> None:
        self.assertEqual(OCR["tokens_differing"], 1)
        self.assertTrue(OCR["ocr_explainable"])
        self.assertEqual((OCR["declared_token"], OCR["document_token"]), ("Minh", "Mlnh"))

    def test_different_people_are_not_explainable(self) -> None:
        self.assertFalse(REAL["ocr_explainable"])
        self.assertEqual(REAL["tokens_differing"], 3)

    def test_two_token_or_non_glyph_differences_are_not_explainable(self) -> None:
        self.assertFalse(name_diff("Tran Minh Anh", "Tran Mlnh Arh")["ocr_explainable"])
        self.assertFalse(name_diff("Tran Minh Anh", "Tran My Anh")["ocr_explainable"])
        self.assertFalse(name_diff("Tran Minh Anh", "Tran Minh")["ocr_explainable"])


class ExpertRuleBoundaryTests(unittest.TestCase):
    def test_fires_only_inside_every_bound(self) -> None:
        self.assertEqual(evaluate(facts(docs(diff=OCR))).rule_id, "R-ID-EXP-01")
        outside = {
            "real mismatch": docs(diff=REAL),
            "liveness failed": docs(liveness=False, diff=OCR),
            "tampered": docs(tamper=0.10, diff=OCR),
            "also missing evidence": docs(missing=["proof_of_address"], diff=OCR),
            "no name_diff signal": docs(),
            "no tamper signal": {k: v for k, v in docs(diff=OCR).items() if k != "tamper_score"},
        }
        for label, d in outside.items():
            v = evaluate(facts(d))
            self.assertEqual((v.rule_id, v.outcome), ("R-ID-01", Outcome.MANUAL_REVIEW), label)

    def test_sanctions_hard_stop_still_wins(self) -> None:
        v = evaluate(facts(docs(diff=OCR), score=0.85))
        self.assertEqual((v.rule_id, v.outcome, v.action), ("R-AML-01", Outcome.ESCALATE_COMPLIANCE, None))

    def test_verdict_requests_reupload_and_renders_in_both_languages(self) -> None:
        v = evaluate(facts(docs(diff=OCR)))
        self.assertEqual((v.outcome, v.action), (Outcome.REQUEST_EVIDENCE, "request_document"))
        self.assertEqual(v.reason_params["fields"], ["id_document_reupload"])
        self.assertEqual(v.cites, "KYC-IDENTITY-12")
        en = i18n.render_reason(v.reason_key, v.reason_params, "en")
        vi = i18n.render_reason(v.reason_key, v.reason_params, "vi")
        self.assertIn("'Minh' vs 'Mlnh'", en)
        self.assertIn("KYC-IDENTITY-12", vi)

    def test_rule_is_attributed_to_an_expert_source(self) -> None:
        r = Ontology.from_dict(RAW).rule("R-ID-EXP-01")
        self.assertEqual(r.source["kind"], "expert")
        self.assertFalse(r.hard_stop)

    def test_reupload_resolves_the_mismatch_in_the_tool(self) -> None:
        tools = DomainTools()
        before = tools.call("verify_documents", "t", {"case_id": "KYC-1043"}).output
        after = tools.call("verify_documents", "t", {"case_id": "KYC-1043", "submitted_documents": [
            {"type": "id_document_reupload", "status": "verified"}]}).output
        self.assertFalse(before["name_match"])
        self.assertTrue(before["name_diff"]["ocr_explainable"])
        self.assertTrue(after["name_match"])
        self.assertEqual(after["name_diff"]["tokens_differing"], 0)


class MissingPolicyLoaderTests(unittest.TestCase):
    def test_missing_false_under_not_is_rejected(self) -> None:
        data = copy.deepcopy(RAW)
        r = next(r for r in data["cognitive"]["rules"] if r["id"] == "R-ID-EXP-01")
        r["when"]["all"].append({"not": {"fact": "verify_documents.name_diff.diacritic_only", "op": "is_true", "missing": "false"}})
        with self.assertRaisesRegex(OntologyError, "absent evidence"):
            Ontology.from_dict(data)

    def test_unknown_missing_policy_and_leaf_keys_are_rejected(self) -> None:
        for bad in ({"missing": "true"}, {"default": 1}):
            data = copy.deepcopy(RAW)
            r = next(r for r in data["cognitive"]["rules"] if r["id"] == "R-ID-01")
            r["when"]["any"][0].update(bad)
            with self.assertRaises(OntologyError):
                Ontology.from_dict(data)

    def test_core_facts_still_fail_closed_when_missing(self) -> None:
        f = facts(docs(diff=OCR))
        del f["verify_documents"]["name_match"]
        with self.assertRaises(FactMissingError):
            evaluate(f)


if __name__ == "__main__":
    unittest.main()
