"""Local / sovereign planner: same contract against an OpenAI-compatible local endpoint."""
import os
import unittest
from unittest.mock import patch

import httpx

from app.agent import KYCExceptionAgent
from app.domain import Outcome
from app.planner import DEFAULT_LOCAL_BASE_URL, LocalPlanner, local_endpoint_status, select_planner


def fake_local_server(outcome: str, action):
    """httpx transport that speaks just enough of the OpenAI chat API for structured output."""
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen2.5:7b-instruct"}]})
        body = json.loads(request.content)
        tools = body.get("tools") or []
        args = json.dumps({"outcome": outcome, "action": action, "rationale": "Per KYC-EVIDENCE-07.", "confidence": 0.8})
        message = {"role": "assistant", "content": None}
        if tools:
            name = tools[0]["function"]["name"]
            message["tool_calls"] = [{"id": "c1", "type": "function", "function": {"name": name, "arguments": args}}]
        else:
            message["content"] = args
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tools else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        })
    return httpx.MockTransport(handler)


class LocalPlannerTests(unittest.TestCase):
    def test_defaults_and_env_overrides(self) -> None:
        with patch.dict(os.environ, {"LOCAL_LLM_BASE_URL": "", "LOCAL_LLM_MODEL": ""}):
            planner = LocalPlanner()
        self.assertEqual((planner.base_url, planner.model, planner.name),
                         (DEFAULT_LOCAL_BASE_URL, "qwen2.5:7b-instruct", "local:qwen2.5:7b-instruct"))
        with patch.dict(os.environ, {"LOCAL_LLM_BASE_URL": "http://gpu-box:8000/v1/", "LOCAL_LLM_MODEL": "llama3.1:8b"}):
            planner = LocalPlanner()
        self.assertEqual((planner.base_url, planner.model), ("http://gpu-box:8000/v1", "llama3.1:8b"))

    def test_unreachable_endpoint_is_a_configuration_error_before_the_graph_runs(self) -> None:
        with patch.dict(os.environ, {"LOCAL_LLM_BASE_URL": "http://127.0.0.1:9/v1"}):
            self.assertEqual(local_endpoint_status(timeout=0.5), (False, []))
            with self.assertRaisesRegex(ValueError, "not reachable"):
                select_planner("local")

    def test_api_rejects_unreachable_local_endpoint_before_running(self) -> None:
        from fastapi.testclient import TestClient
        from app.api import app

        with patch.dict(os.environ, {"REDIS_URL": "", "MLFLOW_TRACKING_URI": "",
                                  "LOCAL_LLM_BASE_URL": "http://127.0.0.1:9/v1"}):
            with TestClient(app) as client:
                response = client.post("/api/run", json={"case_id": "KYC-1044", "planner_mode": "local"})
                self.assertEqual((response.status_code, set(response.json())), (400, {"error"}))
                self.assertIn("not reachable", response.json()["error"])

    def test_governed_run_through_a_local_endpoint(self) -> None:
        transport = fake_local_server("REQUEST_EVIDENCE", "request_document")
        import openai

        real = openai.OpenAI.__init__

        def with_transport(self, *args, **kwargs):
            kwargs["http_client"] = httpx.Client(transport=transport)
            real(self, *args, **kwargs)

        with patch.object(openai.OpenAI, "__init__", with_transport):
            planner = LocalPlanner(base_url="http://local.test/v1")
            decision = KYCExceptionAgent().run("KYC-1042", planner=planner)
        self.assertEqual(decision.outcome, Outcome.REQUEST_EVIDENCE)
        self.assertEqual(decision.model, "local:qwen2.5:7b-instruct")
        self.assertIsNone(decision.guardrail_override)
        self.assertEqual(decision.planner_usage.get("total_tokens"), 120)

    def test_a_wrong_local_proposal_is_still_overridden(self) -> None:
        transport = fake_local_server("CLEAR", None)
        import openai

        real = openai.OpenAI.__init__

        def with_transport(self, *args, **kwargs):
            kwargs["http_client"] = httpx.Client(transport=transport)
            real(self, *args, **kwargs)

        with patch.object(openai.OpenAI, "__init__", with_transport):
            decision = KYCExceptionAgent().run("KYC-1044", planner=LocalPlanner(base_url="http://local.test/v1"))
        self.assertEqual(decision.outcome, Outcome.ESCALATE_COMPLIANCE)
        self.assertIsNotNone(decision.guardrail_override)


if __name__ == "__main__":
    unittest.main()
