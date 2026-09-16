from pathlib import Path
import re
import unittest


DECK = Path(__file__).resolve().parents[1] / "slides" / "index-concise.html"


class ConciseDeckTests(unittest.TestCase):
    def test_concise_deck_has_a_focused_demo_narrative(self) -> None:
        """The new deck keeps the demo's safety proof while dropping biography."""
        html = DECK.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r'<section class="slide">', html)), 15)
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
        for step in ("Grounding fan-in", "Policy precheck", "OpenRouter", "Reconcile guard", "Compliance stop"):
            self.assertIn(step, html[walkthrough:trust_boundary])

    def test_workflow_slide_shows_fanout_fanin_and_explicit_routes(self) -> None:
        html = DECK.read_text(encoding="utf-8")
        workflow = html.index("The control loop")
        walkthrough = html.index("Workflow walkthrough: KYC-1044")
        workflow_markup = html[workflow:walkthrough]
        for label in (
            "Parallel grounding",
            "Evidence quality gate",
            "OpenRouter",
            "Policy precheck",
            "Request evidence",
            "Manual review",
            "Compliance stop",
            "Resume cycle",
        ):
            self.assertIn(label, workflow_markup)
        self.assertIn('class="fanout"', workflow_markup)
        self.assertIn('class="route-list"', workflow_markup)

    def test_llm_role_slide_follows_the_case_walkthrough(self) -> None:
        html = DECK.read_text(encoding="utf-8")
        walkthrough = html.index("Workflow walkthrough: KYC-1044")
        llm_role = html.index("What the LLM does")
        trust_boundary = html.index("Trust boundary")
        self.assertLess(walkthrough, llm_role)
        self.assertLess(llm_role, trust_boundary)
        for detail in ("System prompt", "User prompt", "Model output", "outcome: \"CLEAR\""):
            self.assertIn(detail, html[llm_role:trust_boundary])

    def test_mlflow_slide_shows_a_concrete_guardrail_trace(self) -> None:
        html = DECK.read_text(encoding="utf-8")
        mlflow = html.index("Trace example: KYC-1044")
        trust_boundary = html.index("Trust boundary")
        self.assertLess(mlflow, trust_boundary)
        for detail in ("compromised_demo", "Proposal: CLEAR (97%)", "Reconcile guard: ESCALATE_COMPLIANCE", "No approval token"):
            self.assertIn(detail, html[mlflow:trust_boundary])
        self.assertIn('class="trace-list compact"', html[mlflow:trust_boundary])

    def test_concise_deck_keeps_presentation_controls(self) -> None:
        html = DECK.read_text(encoding="utf-8")
        for control in ("ArrowRight", "ArrowLeft", "requestFullscreen", "@media print"):
            self.assertIn(control, html)


if __name__ == "__main__":
    unittest.main()
