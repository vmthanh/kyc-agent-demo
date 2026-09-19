import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import app
from app.domain import AgentDecision, Outcome, WorkflowStatus
from app.tools import ToolError


def a_decision(**overrides) -> AgentDecision:
    """A minimal valid decision. Routes declare `response_model=AgentDecision`,
    so a stub must actually satisfy that schema -- a bare object would now fail
    response validation rather than pass through."""
    fields = dict(
        case_id="KYC-1045",
        decision_id="abc123",
        outcome=Outcome.CLEAR,
        outcome_label="Clear",
        summary="ok",
        proposal_confidence=0.9,
        risk_level="LOW",
        risk_label="Low",
        facts=[],
        citations=[],
        tool_calls=[],
        trace=[],
        model="stub",
        llm_rationale="because",
        workflow_status=WorkflowStatus.COMPLETED,
    )
    fields.update(overrides)
    return AgentDecision(**fields)


class WorkflowAPITests(unittest.TestCase):
    def setUp(self) -> None:
        # Lifespan must be deterministic and infrastructure-free even when a
        # developer shell has optional runtime integrations configured.
        self.environment = patch.dict(
            os.environ, {"REDIS_URL": "", "MLFLOW_TRACKING_URI": ""}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(app)
        # TestClient starts a FastAPI lifespan only when entered. The lifespan
        # owns app.state.agent and the checkpointer ExitStack.
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def agent(self):
        return app.state.agent

    # --- behavior preserved from the stdlib server ---

    def test_resume_endpoint_passes_structured_response(self) -> None:
        documents = [{"type": "proof_of_address", "status": "verified"}]
        with patch.object(self.agent(), "resume", return_value=a_decision()) as resume:
            response = self.client.post(
                "/api/resume",
                json={"interrupt_key": "doc-1", "response": {"documents": documents}, "lang": "en"},
            )
        self.assertEqual(response.status_code, 200)
        resume.assert_called_once_with("doc-1", {"documents": documents}, lang="en")

    def test_missing_live_key_is_a_client_visible_configuration_error(self) -> None:
        with patch.object(self.agent(), "run", side_effect=ValueError("OPENROUTER_API_KEY is required")):
            response = self.client.post("/api/run", json={"case_id": "KYC-1045", "planner_mode": "normal"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "OPENROUTER_API_KEY is required")

    def test_resume_rejects_non_object_response(self) -> None:
        response = self.client.post("/api/resume", json={"interrupt_key": "x", "response": ["bad"]})
        self.assertEqual(response.status_code, 400)

    def test_run_rejects_non_string_planner_mode(self) -> None:
        response = self.client.post("/api/run", json={"case_id": "KYC-1045", "planner_mode": []})
        self.assertEqual(response.status_code, 400)

    def test_run_rejects_an_unknown_planner_mode(self) -> None:
        response = self.client.post("/api/run", json={"case_id": "KYC-1045", "planner_mode": "bogus"})
        self.assertEqual(response.status_code, 400)

    def test_missing_run_case_id_is_bad_request(self) -> None:
        response = self.client.post("/api/run", json={"planner_mode": "normal"})
        self.assertEqual(response.status_code, 400)

    def test_legacy_heuristic_planner_is_not_treated_as_new_mode(self) -> None:
        with patch.object(self.agent(), "run", return_value=a_decision()) as run:
            response = self.client.post("/api/run", json={"case_id": "KYC-1045", "planner": "heuristic"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(run.call_args.kwargs["planner_name"], "heuristic")
        self.assertEqual(run.call_args.kwargs["planner_mode"], "heuristic")

    def test_array_json_body_is_a_json_bad_request(self) -> None:
        response = self.client.post("/api/run", json=[])
        self.assertEqual(response.status_code, 400)

    def test_legacy_planner_must_be_a_string(self) -> None:
        response = self.client.post("/api/run", json={"case_id": "KYC-1045", "planner": {}})
        self.assertEqual(response.status_code, 400)

    # --- new behavior ---

    def test_validation_errors_use_the_flat_error_shape_not_fastapi_detail(self) -> None:
        response = self.client.post("/api/run", json={})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("error", body)
        self.assertIsInstance(body["error"], str)
        self.assertNotIn("detail", body)

    def test_unknown_field_is_rejected(self) -> None:
        response = self.client.post("/api/run", json={"case_id": "KYC-1045", "langg": "en"})
        self.assertEqual(response.status_code, 400)

    def test_stale_interrupt_key_is_not_found(self) -> None:
        with patch.object(self.agent(), "resume", side_effect=KeyError("Unknown or already resolved interrupt")):
            response = self.client.post("/api/resume", json={"interrupt_key": "gone", "response": {}})
        self.assertEqual(response.status_code, 404)

    def test_unknown_case_is_not_found(self) -> None:
        with patch.object(self.agent(), "run", side_effect=ToolError("Unknown case: KYC-9999")):
            response = self.client.post("/api/run", json={"case_id": "KYC-9999"})
        self.assertEqual(response.status_code, 404)

    def test_reject_endpoint_passes_the_review_reason_to_the_agent(self) -> None:
        rejected = a_decision(review_result={"status": "rejected", "reason": "Evidence is too old"})
        with patch.object(self.agent(), "reject", return_value=rejected) as reject:
            response = self.client.post(
                "/api/reject",
                json={"approval_key": "approval-1", "reason": "Evidence is too old", "lang": "en"},
            )
        self.assertEqual(response.status_code, 200)
        reject.assert_called_once_with("approval-1", "Evidence is too old", lang="en")
        self.assertEqual(response.json()["review_result"]["reason"], "Evidence is too old")

    def test_empty_rejection_reason_is_bad_request(self) -> None:
        response = self.client.post("/api/reject", json={"approval_key": "k", "reason": "   "})
        self.assertEqual(response.status_code, 400)

    def test_successful_response_conforms_to_the_decision_schema(self) -> None:
        with patch.object(self.agent(), "run", return_value=a_decision()):
            response = self.client.post("/api/run", json={"case_id": "KYC-1045"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), set(AgentDecision.__dataclass_fields__))
        self.assertEqual(body["outcome"], "CLEAR")
        self.assertEqual(body["workflow_status"], "COMPLETED")

    def test_health_reports_the_checkpointer_backend(self) -> None:
        body = self.client.get("/health").json()
        self.assertEqual(body, {"status": "ok", "checkpointer": "memory"})

    def test_cases_are_listed(self) -> None:
        body = self.client.get("/api/cases").json()
        self.assertTrue(body)
        self.assertEqual(set(body[0]), {"case_id", "title", "signal"})


if __name__ == "__main__":
    unittest.main()
