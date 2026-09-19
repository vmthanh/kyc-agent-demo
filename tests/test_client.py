import json
import unittest

import httpx

from app.client import KYCAPIError, KYCClient
from app.domain import AgentDecision, Outcome, PendingTaskKind, PendingTask, WorkflowStatus


def a_decision_body() -> dict:
    decision = AgentDecision(
        case_id="KYC-1045",
        decision_id="abc123",
        outcome=Outcome.REQUEST_EVIDENCE,
        outcome_label="Request evidence",
        summary="need docs",
        proposal_confidence=0.7,
        risk_level="MEDIUM",
        risk_label="Medium",
        facts=["f"],
        citations=[],
        tool_calls=[],
        trace=[],
        model="stub",
        llm_rationale="because",
        workflow_status=WorkflowStatus.AWAITING_APPROVAL,
        pending_task=PendingTask(
            PendingTaskKind.ACTION_APPROVAL, "key-1", "t", "m", {}, ["approved"]
        ),
    )
    return decision.to_dict()


class KYCClientTests(unittest.TestCase):
    def client(self, handler) -> KYCClient:
        client = KYCClient("http://testserver", transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def test_run_posts_the_expected_body(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["json"] = json.loads(request.content)
            return httpx.Response(200, json=a_decision_body())

        client = self.client(handler)
        client.run("KYC-1045", planner_mode="compromised_demo", lang="vi")
        self.assertEqual(seen["url"], "http://testserver/api/run")
        self.assertEqual(
            seen["json"],
            {"case_id": "KYC-1045", "planner_mode": "compromised_demo", "lang": "vi"},
        )

    def test_responses_rehydrate_into_a_decision_with_real_enums(self) -> None:
        client = self.client(lambda request: httpx.Response(200, json=a_decision_body()))
        decision = client.run("KYC-1045")
        self.assertIsInstance(decision, AgentDecision)
        self.assertIs(decision.outcome, Outcome.REQUEST_EVIDENCE)
        self.assertIs(decision.workflow_status, WorkflowStatus.AWAITING_APPROVAL)
        # The UI compares this with `is`; a plain string would silently fail.
        self.assertIs(decision.pending_task.kind, PendingTaskKind.ACTION_APPROVAL)

    def test_resume_sends_the_interrupt_key_and_response(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["json"] = json.loads(request.content)
            return httpx.Response(200, json=a_decision_body())

        client = self.client(handler)
        client.resume("key-1", {"approved": True}, lang="en")
        self.assertEqual(seen["url"], "http://testserver/api/resume")
        self.assertEqual(
            seen["json"], {"interrupt_key": "key-1", "response": {"approved": True}, "lang": "en"}
        )

    def test_error_bodies_become_a_typed_exception(self) -> None:
        client = self.client(
            lambda request: httpx.Response(404, json={"error": "Unknown or already resolved interrupt"})
        )
        with self.assertRaises(KYCAPIError) as caught:
            client.approve("gone")
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(caught.exception.message, "Unknown or already resolved interrupt")
        self.assertIn("Unknown or already resolved", str(caught.exception))

    def test_a_non_json_error_body_still_raises_cleanly(self) -> None:
        client = self.client(lambda request: httpx.Response(500, text="boom"))
        with self.assertRaises(KYCAPIError) as caught:
            client.list_cases()
        self.assertEqual(caught.exception.status, 500)

    def test_transport_failure_becomes_a_typed_exception(self) -> None:
        def unavailable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = self.client(unavailable)
        with self.assertRaises(KYCAPIError) as caught:
            client.list_cases()
        self.assertIsNone(caught.exception.status)
        self.assertIn("Runtime unavailable", caught.exception.message)

    def test_list_cases_returns_raw_rows(self) -> None:
        rows = [{"case_id": "KYC-1", "title": "t", "signal": "s"}]
        client = self.client(lambda request: httpx.Response(200, json=rows))
        self.assertEqual(client.list_cases(), rows)


if __name__ == "__main__":
    unittest.main()
