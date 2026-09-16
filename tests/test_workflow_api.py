import io
import json
import unittest
from unittest.mock import patch

from app.server import Handler


class WorkflowAPITests(unittest.TestCase):
    def make_handler(self, path: str, payload: dict):
        body = json.dumps(payload).encode()
        handler = object.__new__(Handler)
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.path = path
        handler._json = lambda value, status=200: setattr(handler, "response", (value, status))
        return handler

    def test_resume_endpoint_passes_structured_response(self) -> None:
        handler = self.make_handler("/api/resume", {
            "interrupt_key": "doc-1",
            "response": {"documents": [{"type": "proof_of_address", "status": "verified"}]},
            "lang": "en",
        })
        fake = type("Decision", (), {"to_dict": lambda self: {"workflow_status": "COMPLETED"}})()
        with patch("app.server.AGENT.resume", return_value=fake) as resume:
            handler.do_POST()
        resume.assert_called_once_with("doc-1", {
            "documents": [{"type": "proof_of_address", "status": "verified"}]
        }, lang="en")

    def test_missing_live_key_is_a_client_visible_configuration_error(self) -> None:
        handler = self.make_handler("/api/run", {"case_id": "KYC-1045", "planner_mode": "normal"})
        with patch("app.server.AGENT.run", side_effect=ValueError("OPENROUTER_API_KEY is required")):
            handler.do_POST()
        self.assertEqual(handler.response[1], 400)

    def test_resume_rejects_non_object_response(self) -> None:
        handler = self.make_handler("/api/resume", {"interrupt_key": "x", "response": ["bad"]})
        handler.do_POST()
        self.assertEqual(handler.response[1], 400)

    def test_run_rejects_non_string_planner_mode(self) -> None:
        handler = self.make_handler("/api/run", {"case_id": "KYC-1045", "planner_mode": []})
        handler.do_POST()
        self.assertEqual(handler.response[1], 400)

    def test_missing_run_case_id_is_bad_request(self) -> None:
        handler = self.make_handler("/api/run", {"planner_mode": "normal"})
        handler.do_POST()
        self.assertEqual(handler.response[1], 400)

    def test_legacy_heuristic_planner_is_not_treated_as_new_mode(self) -> None:
        handler = self.make_handler("/api/run", {"case_id": "KYC-1045", "planner": "heuristic"})
        fake = type("Decision", (), {"to_dict": lambda self: {"workflow_status": "COMPLETED"}})()
        with patch("app.server.AGENT.run", return_value=fake) as run:
            handler.do_POST()
        self.assertEqual(handler.response[1], 200)
        self.assertEqual(run.call_args.kwargs["planner_name"], "heuristic")

    def test_array_json_body_is_a_json_bad_request(self) -> None:
        body = json.dumps([]).encode()
        handler = object.__new__(Handler)
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.path = "/api/run"
        handler._json = lambda value, status=200: setattr(handler, "response", (value, status))
        handler.do_POST()
        self.assertEqual(handler.response[1], 400)

    def test_legacy_planner_must_be_a_string(self) -> None:
        handler = self.make_handler("/api/run", {"case_id": "KYC-1045", "planner": {}})
        handler.do_POST()
        self.assertEqual(handler.response[1], 400)
