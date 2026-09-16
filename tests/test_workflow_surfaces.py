import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowSurfaceTests(unittest.TestCase):
    def test_streamlit_offers_only_live_demo_modes(self) -> None:
        source = (ROOT / "app" / "ui.py").read_text()
        self.assertIn('"normal", "compromised_demo"', source)
        self.assertIn("PendingTaskKind.DOCUMENT_SUBMISSION", source)
        self.assertIn("agent.resume", source)

    def test_cli_can_complete_the_evidence_demo_in_one_process(self) -> None:
        source = (ROOT / "app" / "cli.py").read_text()
        self.assertIn("--submit-proof-of-address", source)
        self.assertIn("compromised_demo", source)

    def test_cli_can_acknowledge_operational_handoff(self) -> None:
        source = (ROOT / "app" / "cli.py").read_text()
        self.assertIn("--acknowledge-handoff", source)
        self.assertIn('PendingTaskKind.OPERATIONAL_REVIEW', source)
        self.assertIn('"acknowledged": True', source)
