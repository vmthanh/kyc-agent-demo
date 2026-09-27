"""Presentation contract: current implementation, evidence and portable assets."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "slides" / "index.html"


class MainDeckTests(unittest.TestCase):
    def test_main_deck_is_current_and_candid(self) -> None:
        html = DECK.read_text()
        self.assertEqual(len(re.findall(r'<section class="slide">', html)), 12)
        for claim in ("R-ID-EXP-01@1.0", "KYC-1043", "KYC-1044", "KYC-1046", "Re-test 65 cases",
                      "9 cases would become less safe", "60 practice cases", "71.7%", "80%",
                      "24 safety checks", "not DanaOS", "Not done yet", "not a production accuracy figure"):
            self.assertIn(claim.lower(), html.lower())
        for jargon in ("reconcile_guard", "idempotency", "fan-in", "quorum", "counterfactual"):
            visible = re.sub(r'<aside class="notes".*?</aside>', "", html, flags=re.S)
            self.assertNotIn(jargon, visible.lower(), jargon)

    def test_any_images_resolve_from_the_html_deck(self) -> None:
        html = DECK.read_text()
        images = re.findall(r'<img[^>]+src="([^"]+)"', html)
        for image in images:
            asset = DECK.parent / image
            self.assertTrue(asset.is_file(), asset)
            self.assertGreater(asset.stat().st_size, 10_000, asset)

    def test_recorded_metrics_have_a_source(self) -> None:
        result = (ROOT / "docs" / "results" / "2026-09-27-scorecard-gpt-4o-mini.md").read_text()
        for metric in ("100.0%", "71.7%", "546 of 900", "2 |"):
            self.assertIn(metric, result)


if __name__ == "__main__":
    unittest.main()
