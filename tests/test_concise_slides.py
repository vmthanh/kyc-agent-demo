from pathlib import Path
import re
import unittest


DECK = Path(__file__).resolve().parents[1] / "slides" / "index-concise.html"


class ConciseDeckTests(unittest.TestCase):
    def test_concise_deck_is_a_lean_technical_deck(self) -> None:
        """Round-2 deck trims to a dense, interview-depth set with no biography."""
        html = DECK.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r'<section class="slide">', html)), 9)
        self.assertIn("KYC-1044", html)
        self.assertIn("deterministic guardrail", html)
        self.assertIn("human approval", html)
        self.assertNotIn("Thanh Minh Vo", html)
        self.assertNotIn("50M+", html)

    def test_concise_deck_walks_through_the_adversarial_case_after_the_control_loop(self) -> None:
        html = DECK.read_text(encoding="utf-8")
        control_loop = html.index("The control loop")
        walkthrough = html.index("Workflow walkthrough: KYC-1044")
        trust_boundary = html.index("Trust boundary")
        self.assertLess(control_loop, walkthrough)
        self.assertLess(walkthrough, trust_boundary)
        for step in ("Grounding fan-in", "Policy precheck", "Reconcile guard", "Compliance stop"):
            self.assertIn(step, html[walkthrough:trust_boundary])

    def test_workflow_slide_shows_fanout_fanin_and_explicit_routes(self) -> None:
        html = DECK.read_text(encoding="utf-8")
        workflow = html.index("The control loop")
        walkthrough = html.index("Workflow walkthrough: KYC-1044")
        workflow_markup = html[workflow:walkthrough]
        for label in (
            "Parallel grounding",
            "Evidence gate",
            "Policy precheck",
            "Request evidence",
            "Manual review",
            "Compliance stop",
            "resume cycle",
        ):
            self.assertIn(label, workflow_markup)
        self.assertIn('class="fanout"', workflow_markup)
        self.assertIn('class="route-list"', workflow_markup)

    def test_control_loop_slide_mentions_the_graph_upgrade_inline(self) -> None:
        """The before/after comparison was folded into a one-line banner, not a dedicated slide."""
        html = DECK.read_text(encoding="utf-8")
        control_loop = html.index("The control loop")
        walkthrough = html.index("Workflow walkthrough: KYC-1044")
        control_loop_markup = html[control_loop:walkthrough]
        self.assertNotIn("The upgrade", html)
        self.assertIn("linear 5-node chain", control_loop_markup)

    def test_seams_slide_follows_safe_action_before_evidence_and_evaluation(self) -> None:
        html = DECK.read_text(encoding="utf-8")
        safe_action = html.index("Safe action")
        seams = html.index(">Seams<")
        evidence = html.index("Evidence and evaluation")
        self.assertLess(safe_action, seams)
        self.assertLess(seams, evidence)
        seams_markup = html[seams:evidence]
        for detail in ("Two selectable modes", "In-memory checkpointer", "idempotency", "OpenTelemetry", "Tenancy"):
            self.assertIn(detail, seams_markup)

    def test_concise_deck_keeps_presentation_controls(self) -> None:
        html = DECK.read_text(encoding="utf-8")
        for control in ("ArrowRight", "ArrowLeft", "requestFullscreen", "@media print"):
            self.assertIn(control, html)


if __name__ == "__main__":
    unittest.main()
