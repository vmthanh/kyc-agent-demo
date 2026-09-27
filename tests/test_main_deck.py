"""Presentation contract: current implementation, evidence and portable assets."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "slides" / "index.html"


class MainDeckTests(unittest.TestCase):
    def test_main_deck_is_current_and_candid(self) -> None:
        html = DECK.read_text()
        self.assertEqual(len(re.findall(r'<section class="slide">', html)), 14)
        for claim in ("R-ID-EXP-01@1.0", "KYC-1046", "65 cases", "9 regressions",
                      "60 labelled cases", "71.7%", "228 unit tests", "24 deterministic eval checks",
                      "not DanaOS", "No local model benchmark"):
            self.assertIn(claim.lower(), html.lower())
        self.assertNotIn("139 unit tests", html)
        self.assertNotIn("19 deterministic evals", html)

    def test_committed_images_resolve_from_the_html_deck(self) -> None:
        html = DECK.read_text()
        images = re.findall(r'<img[^>]+src="([^"]+)"', html)
        self.assertGreaterEqual(len(images), 2)
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
