import unittest
from unittest.mock import patch

from app.planner import OpenRouterPlanner, PlannerUnavailableError, select_planner


class OpenRouterWorkflowPlannerTests(unittest.TestCase):
    def test_normal_mode_requires_a_real_key(self) -> None:
        with patch("app.planner.os.getenv", return_value=""):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                select_planner("normal")

    def test_compromised_mode_still_builds_an_openrouter_planner(self) -> None:
        with patch(
            "app.planner.os.getenv",
            side_effect=lambda key, default=None: "test-key" if key == "OPENROUTER_API_KEY" else default,
        ), patch("langchain_openai.ChatOpenAI"):
            planner = select_planner("compromised_demo")
        self.assertIsInstance(planner, OpenRouterPlanner)
        self.assertTrue(planner.compromised)

    def test_schema_failure_is_wrapped_for_graph_retry(self) -> None:
        class BrokenClient:
            def invoke(self, messages):
                raise ValueError("invalid structured output")

        planner = object.__new__(OpenRouterPlanner)
        planner.name = "openrouter:test"
        planner.model = "test"
        planner.compromised = False
        planner._structured_client = BrokenClient()
        with self.assertRaises(PlannerUnavailableError) as raised:
            planner.propose("KYC-1042", {}, [], "note")
        self.assertEqual(raised.exception.category, "ValueError")

    def test_provider_metadata_is_copied_to_usage(self) -> None:
        class Raw:
            usage_metadata = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
            response_metadata = {"usage": {"cost": 0.0012}}

        class Client:
            def invoke(self, messages):
                return {
                    "parsed": type("Proposal", (), {
                        "outcome": "CLEAR", "action": None,
                        "rationale": "Grounded", "confidence": 0.9,
                    })(),
                    "raw": Raw(),
                    "parsing_error": None,
                }

        planner = object.__new__(OpenRouterPlanner)
        planner.name = "openrouter:test"
        planner.model = "test"
        planner.compromised = False
        planner._structured_client = Client()
        proposal = planner.propose("KYC-1042", {}, [], "note")
        self.assertEqual(proposal.usage, {
            "input_tokens": 11, "output_tokens": 7, "total_tokens": 18, "cost": 0.0012,
        })


    def test_string_null_action_is_normalized_so_the_guard_does_not_report_a_false_override(self) -> None:
        from app.domain import Outcome
        from app.policy import PolicyVerdict, guard_verdict

        class Client:
            def __init__(self, action):
                self.action = action

            def invoke(self, messages):
                return {"parsed": type("P", (), {"outcome": "ESCALATE_COMPLIANCE", "action": self.action,
                                                   "rationale": "r", "confidence": 0.9})(),
                        "raw": None, "parsing_error": None}

        verdict = PolicyVerdict(Outcome.ESCALATE_COMPLIANCE, None, "CRITICAL", "sanctions_hit")
        for spelled in (None, "null", "None", " ", "no_action"):
            planner = object.__new__(OpenRouterPlanner)
            planner.name, planner.model, planner.compromised = "openrouter:test", "test", False
            planner._structured_client = Client(spelled)
            proposal = planner.propose("KYC-1044", {}, [], "note")
            self.assertIsNone(proposal.action, spelled)
            self.assertIsNone(guard_verdict(verdict, proposal).override_info, spelled)


    def test_free_text_enum_values_are_malformed_not_opinions(self) -> None:
        class Client:
            def __init__(self, outcome, action):
                self.outcome, self.action = outcome, action

            def invoke(self, messages):
                return {"parsed": type("P", (), {"outcome": self.outcome, "action": self.action,
                                                   "rationale": "r", "confidence": 0.5})(),
                        "raw": None, "parsing_error": None}

        def planner(outcome, action):
            p = object.__new__(OpenRouterPlanner)
            p.name, p.model, p.compromised = "openrouter:test", "test", False
            p._structured_client = Client(outcome, action)
            return p

        for outcome, action, category in (("PENDING", None, "InvalidOutcome"),
                                          ("REQUEST_EVIDENCE", "Request proof of address", "InvalidAction")):
            with self.assertRaises(PlannerUnavailableError) as raised:
                planner(outcome, action).propose("KYC-1042", {}, [], "note")
            self.assertEqual(raised.exception.category, category)
        ok = planner(" request_evidence ", "request_document").propose("KYC-1042", {}, [], "note")
        self.assertEqual(ok.outcome, "REQUEST_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
