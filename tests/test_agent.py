import io
import json
import threading
from pathlib import Path
import unittest
from unittest.mock import patch

from app import i18n
from app.agent import KYCExceptionAgent
from app.domain import LLMProposal, Outcome
from app.planner import OpenRouterPlanner
from app.policy import evaluate, guard
from app.server import Handler
from app.tools import DomainTools


def run_concurrently(worker, count: int) -> list:
    """Run `worker(i)` in `count` threads and return results in call order.

    A race-condition test that swallows worker exceptions can pass for the
    wrong reason: if most threads error out and only one survives, checks
    like "exactly one ticket id" look identical to a correctly-locked run.
    This helper pre-sizes the result slots and re-raises the first captured
    exception, so a test can only pass if every worker actually completed.
    """
    results: list = [None] * count
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def run(i: int) -> None:
        try:
            results[i] = worker(i)
        except BaseException as exc:  # a test helper must not swallow anything
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    return results


class PolicyTests(unittest.TestCase):
    """Pure, fast tests of the deterministic safety boundary -- no graph, no I/O."""

    def facts(self, **overrides):
        base = {
            "verify_documents": {"name_match": True, "liveness_passed": True, "missing_fields": []},
            "screen_sanctions": {"match_score": 0.04, "candidate": None},
            "get_risk_profile": {"level": "LOW", "score": 0.1},
        }
        base.update(overrides)
        return base

    def test_clean_case_clears_with_no_action(self) -> None:
        verdict = evaluate(self.facts())
        self.assertEqual(verdict.outcome, Outcome.CLEAR)
        self.assertIsNone(verdict.action)

    def test_missing_evidence_requests_document(self) -> None:
        docs = {"name_match": True, "liveness_passed": True, "missing_fields": ["proof_of_address"]}
        verdict = evaluate(self.facts(verify_documents=docs))
        self.assertEqual(verdict.outcome, Outcome.REQUEST_EVIDENCE)
        self.assertEqual(verdict.action, "request_document")

    def test_identity_mismatch_forces_manual_review(self) -> None:
        docs = {"name_match": False, "liveness_passed": True, "missing_fields": []}
        verdict = evaluate(self.facts(verify_documents=docs))
        self.assertEqual(verdict.outcome, Outcome.MANUAL_REVIEW)

    def test_sanctions_hit_overrides_missing_evidence(self) -> None:
        """Sanctions must win even when evidence is also incomplete."""
        docs = {"name_match": True, "liveness_passed": True, "missing_fields": ["proof_of_address"]}
        sanctions = {"match_score": 0.95, "candidate": "X / List-1"}
        verdict = evaluate(self.facts(verify_documents=docs, screen_sanctions=sanctions))
        self.assertEqual(verdict.outcome, Outcome.ESCALATE_COMPLIANCE)
        self.assertIsNone(verdict.action)

    def test_guard_lets_a_correct_proposal_through_untouched(self) -> None:
        proposal = LLMProposal("CLEAR", None, "looks fine", 0.9, "test-model")
        result = guard(self.facts(), proposal)
        self.assertIsNone(result.override_info)

    def test_guard_overrides_a_proposal_that_ignores_a_sanctions_hit(self) -> None:
        """The core safety property: a model (compromised, jailbroken, or just
        wrong) can never talk its way past a mandatory sanctions escalation."""
        sanctions = {"match_score": 0.95, "candidate": "X / List-1"}
        proposal = LLMProposal("CLEAR", None, "case note says this is cleared, approve it", 0.99, "test-model")
        result = guard(self.facts(screen_sanctions=sanctions), proposal)
        self.assertEqual(result.verdict.outcome, Outcome.ESCALATE_COMPLIANCE)
        self.assertIsNotNone(result.override_info)


class ToolTests(unittest.TestCase):
    def test_execute_approved_action_is_idempotent(self) -> None:
        tools = DomainTools()
        first = tools.execute_approved_action({"action": "request_document"}, "same-key")
        second = tools.execute_approved_action({"action": "request_document"}, "same-key")
        self.assertEqual(first["ticket_id"], second["ticket_id"])
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])

    def test_executed_action_echoes_the_full_approved_payload(self) -> None:
        """A reviewer approves specific parameters (which documents, which
        case), not just an action name -- the ticket must reflect them."""
        tools = DomainTools()
        result = tools.execute_approved_action(
            {"case_id": "KYC-1042", "action": "request_document", "documents": ["proof_of_address"]},
            "some-key",
        )
        self.assertEqual(result["details"]["case_id"], "KYC-1042")
        self.assertEqual(result["details"]["documents"], ["proof_of_address"])

    def test_case_note_is_exposed_separately_from_typed_facts(self) -> None:
        tools = DomainTools()
        note = tools.get_case_note("KYC-1044")
        self.assertIn("false positive", note)
        sanctions = tools.call("screen_sanctions", "test", {"case_id": "KYC-1044"}).output
        self.assertNotIn("case_note", sanctions)

    def test_concurrent_approvals_with_the_same_key_never_double_execute(self) -> None:
        """ThreadingHTTPServer serves concurrent requests against one shared
        DomainTools. Twenty threads racing on the same idempotency key must
        mint exactly one ticket, not one-per-thread -- and every thread must
        actually complete (a swallowed exception could look like safety)."""
        tools = DomainTools()
        results = run_concurrently(
            lambda i: tools.execute_approved_action({"action": "request_document"}, "race-key"), 20
        )
        self.assertEqual(len(results), 20)
        self.assertEqual(len({r["ticket_id"] for r in results}), 1)
        self.assertEqual(sum(1 for r in results if r["replayed"]), 19)

    def test_concurrent_approvals_with_different_keys_never_collide_tickets(self) -> None:
        tools = DomainTools()
        results = run_concurrently(
            lambda i: tools.execute_approved_action({"action": "request_document"}, f"key-{i}"), 20
        )
        self.assertEqual(len(results), 20)
        ticket_ids = [r["ticket_id"] for r in results]
        self.assertEqual(len(ticket_ids), len(set(ticket_ids)))


class HTTPServerTests(unittest.TestCase):
    def test_unknown_case_returns_a_json_not_found_response(self) -> None:
        """Client input errors must not terminate the HTTP connection."""
        body = json.dumps({"case_id": "KYC-UNKNOWN", "planner": "heuristic"}).encode()
        handler = object.__new__(Handler)
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.path = "/api/run"
        responses = []
        handler._json = lambda value, status=200: responses.append((value, status))

        handler.do_POST()

        self.assertEqual(responses, [({"error": "Unknown case: KYC-UNKNOWN"}, 404)])

    def test_reject_endpoint_passes_the_review_reason_to_the_agent(self) -> None:
        body = json.dumps({"approval_key": "approval-1", "reason": "Evidence is too old", "lang": "en"}).encode()
        handler = object.__new__(Handler)
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.path = "/api/reject"
        responses = []
        handler._json = lambda value, status=200: responses.append((value, status))

        class FakeDecision:
            def to_dict(self):
                return {"review_result": {"status": "rejected", "reason": "Evidence is too old"}}

        with patch("app.server.AGENT.reject", return_value=FakeDecision()) as reject:
            handler.do_POST()

        reject.assert_called_once_with("approval-1", "Evidence is too old", lang="en")
        self.assertEqual(responses[0][0]["review_result"]["reason"], "Evidence is too old")


class StaticDemoTests(unittest.TestCase):
    def test_approval_is_bound_to_the_rendered_case_and_stale_responses_are_ignored(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()
        self.assertIn("${caseLabel} ${esc(d.case_id)}", source)
        self.assertIn("$('#case').addEventListener('change', clearDecision);", source)
        self.assertIn("if (version !== viewVersion) return;", source)

    def test_rejection_requires_a_reason_and_renders_a_distinct_outcome(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()
        self.assertIn("id=\"reject-reason\"", source)
        self.assertIn("/api/reject", source)
        self.assertIn("Action rejected", source)


class AgentEndToEndTests(unittest.TestCase):
    """Runs the full LangGraph pipeline with the offline heuristic planner --
    deterministic, no network, matches what a live demo runs by default."""

    def setUp(self) -> None:
        self.agent = KYCExceptionAgent()

    def run_heuristic(self, case_id: str):
        return self.agent.run(case_id, planner_name="heuristic")

    def test_missing_evidence_requires_approval(self) -> None:
        result = self.run_heuristic("KYC-1042")
        self.assertEqual(result.outcome, Outcome.REQUEST_EVIDENCE)
        self.assertIsNotNone(result.approval)
        self.assertIsNone(result.executed_action)

    def test_approval_carries_the_actual_requested_documents(self) -> None:
        """The reviewer must see (and the gateway must receive) which
        documents are being requested, not just the action's name."""
        result = self.run_heuristic("KYC-1042")
        action_payload = result.approval.payload["action_payload"]
        self.assertEqual(action_payload["case_id"], "KYC-1042")
        self.assertEqual(action_payload["documents"], ["proof_of_address"])
        approved = self.agent.approve(result.approval.approval_key)
        self.assertEqual(approved.executed_action["details"]["documents"], ["proof_of_address"])

    def test_identity_mismatch_routes_to_manual_review(self) -> None:
        result = self.run_heuristic("KYC-1043")
        self.assertEqual(result.outcome, Outcome.MANUAL_REVIEW)

    def test_sanctions_hit_blocks_automatic_action(self) -> None:
        result = self.run_heuristic("KYC-1044")
        self.assertEqual(result.outcome, Outcome.ESCALATE_COMPLIANCE)
        self.assertIsNone(result.approval)

    def test_clean_case_needs_no_approval(self) -> None:
        result = self.run_heuristic("KYC-1045")
        self.assertEqual(result.outcome, Outcome.CLEAR)
        self.assertIsNone(result.approval)
        self.assertIsNone(result.executed_action)

    def test_resolved_interrupt_cannot_be_approved_twice(self) -> None:
        result = self.run_heuristic("KYC-1042")
        key = result.approval.approval_key
        self.agent.approve(key)
        with self.assertRaisesRegex(KeyError, "resolved"):
            self.agent.approve(key)

    def test_rejecting_an_action_records_the_reason_without_a_side_effect(self) -> None:
        result = self.run_heuristic("KYC-1042")

        rejected = self.agent.reject(result.approval.approval_key, "Document is too old")

        self.assertIsNone(rejected.executed_action)
        self.assertEqual(rejected.review_result, {"status": "rejected", "reason": "Document is too old"})
        self.assertIsNone(rejected.approval)
        self.assertTrue(any("Document is too old" in event.detail for event in rejected.trace))

    def test_rejection_requires_a_reason(self) -> None:
        result = self.run_heuristic("KYC-1042")
        with self.assertRaisesRegex(ValueError, "reason"):
            self.agent.reject(result.approval.approval_key, "   ")

    def test_two_runs_of_the_same_case_get_independent_approval_handles(self) -> None:
        """Running the same case+action twice before approving either one
        must not let the second run's approval silently replace the first's
        (the old case+action-only key was ambiguous here)."""
        first_run = self.run_heuristic("KYC-1042")
        second_run = self.run_heuristic("KYC-1042")
        self.assertNotEqual(first_run.approval.approval_key, second_run.approval.approval_key)
        # Both are independently resumable.
        self.assertIsNotNone(self.agent.approve(first_run.approval.approval_key).executed_action)
        self.assertIsNotNone(self.agent.approve(second_run.approval.approval_key).executed_action)

    def test_switching_planner_between_runs_does_not_break_approval(self) -> None:
        """A single shared agent must resolve approvals correctly even when
        different runs used different planners (the old per-planner-agent
        design made this ambiguous)."""
        heuristic_result = self.agent.run("KYC-1042", planner_name="heuristic")
        adversarial_result = self.agent.run("KYC-1044", planner_name="adversarial")
        self.assertIsNotNone(self.agent.approve(heuristic_result.approval.approval_key).executed_action)
        self.assertIsNone(adversarial_result.approval)  # sanctions case, nothing to approve

    def test_adversarial_planner_is_overridden_by_the_guardrail(self) -> None:
        """End-to-end version of the red-team scenario: even when the wired-in
        planner is compromised and recommends clearing a sanctions hit, the
        graph's guard node forces the mandated outcome and records why."""
        result = self.agent.run("KYC-1044", planner_name="adversarial")
        self.assertEqual(result.outcome, Outcome.ESCALATE_COMPLIANCE)
        self.assertIsNotNone(result.guardrail_override)
        self.assertIsNone(result.approval)

    def test_confidence_reflects_the_proposal_not_the_enforced_outcome(self) -> None:
        """A misbehaving model's high self-reported confidence in a REJECTED
        proposal must never be presented as confidence in the enforced
        outcome -- it is only ever labeled as the planner's own number,
        alongside the override that replaced it."""
        result = self.agent.run("KYC-1044", planner_name="adversarial")
        self.assertAlmostEqual(result.proposal_confidence, 0.97)
        self.assertIsNotNone(result.guardrail_override)
        self.assertEqual(result.outcome, Outcome.ESCALATE_COMPLIANCE)

    def test_planner_runtime_failure_is_strictly_degraded(self) -> None:
        """Provider failure retries three times and never silently falls back."""

        class BrokenPlanner:
            name = "broken"
            calls = 0

            def propose(self, *args):
                self.calls += 1
                from app.planner import PlannerUnavailableError
                raise PlannerUnavailableError("simulated network failure")

        planner = BrokenPlanner()
        result = self.agent.run("KYC-1042", planner=planner)
        self.assertEqual(planner.calls, 3)
        self.assertEqual(result.outcome, Outcome.MANUAL_REVIEW)
        self.assertEqual(result.workflow_status.value, "AWAITING_OPERATIONS")

    def test_vietnamese_rendering_is_offline_and_stable(self) -> None:
        """Templated content (summary, override, heuristic rationale, policy
        citations) must render in Vietnamese with no network dependency."""
        result = self.agent.run("KYC-1044", planner_name="adversarial", lang="vi")
        self.assertEqual(result.lang, "vi")
        self.assertEqual(result.outcome, Outcome.ESCALATE_COMPLIANCE)
        self.assertIn("Tuân thủ", result.summary)
        self.assertIn("Tuân thủ", result.guardrail_override)
        self.assertIn("báo động giả", result.llm_rationale)
        self.assertTrue(result.rationale_translated)
        self.assertTrue(any("trừng phạt" in c.excerpt for c in result.citations))

    def test_relocalize_switches_language_of_an_existing_decision_in_place(self) -> None:
        """Switching the UI's language after a decision already exists must
        re-render the same decision, not require a fresh run and not lose a
        pending approval. No new tool calls or trace entries may appear."""
        en_result = self.run_heuristic("KYC-1042")
        trace_len_before = len(en_result.trace)
        vi_result = self.agent.relocalize(en_result.decision_id, "vi")

        self.assertEqual(vi_result.decision_id, en_result.decision_id)
        self.assertEqual(vi_result.outcome, en_result.outcome)
        self.assertEqual(len(vi_result.trace), trace_len_before)  # pure re-render, no re-execution
        self.assertIn("chứng minh địa chỉ", vi_result.summary)
        self.assertIn("chứng minh địa chỉ", vi_result.facts[-1])
        # The pending approval survives relocalization and is still resumable.
        self.assertEqual(vi_result.approval.approval_key, en_result.approval.approval_key)
        approved = self.agent.approve(vi_result.approval.approval_key)
        self.assertIsNotNone(approved.executed_action)

    def test_relocalize_after_approval_reflects_the_executed_action(self) -> None:
        result = self.run_heuristic("KYC-1042")
        approved_en = self.agent.approve(result.approval.approval_key)
        approved_vi = self.agent.relocalize(approved_en.decision_id, "vi")
        self.assertEqual(approved_vi.executed_action["ticket_id"], approved_en.executed_action["ticket_id"])
        self.assertIsNone(approved_vi.approval)

    def test_concurrent_runs_across_different_cases_are_safe(self) -> None:
        """A single agent instance backs a ThreadingHTTPServer; concurrent
        runs on different cases must not corrupt the shared graph cache, and
        every run must actually complete (a swallowed exception could look
        like a passing, if incomplete, result)."""
        case_ids = ["KYC-1042", "KYC-1043", "KYC-1044", "KYC-1045"] * 5
        results = run_concurrently(
            lambda i: (case_ids[i], self.agent.run(case_ids[i], planner_name="heuristic").outcome),
            len(case_ids),
        )
        self.assertEqual(len(results), len(case_ids))
        outcomes = dict(results)
        self.assertEqual(outcomes["KYC-1042"], Outcome.REQUEST_EVIDENCE)
        self.assertEqual(outcomes["KYC-1043"], Outcome.MANUAL_REVIEW)
        self.assertEqual(outcomes["KYC-1044"], Outcome.ESCALATE_COMPLIANCE)
        self.assertEqual(outcomes["KYC-1045"], Outcome.CLEAR)

    def test_replaying_an_resolved_interrupt_is_rejected(self) -> None:
        result = self.run_heuristic("KYC-1042")
        key = result.approval.approval_key
        self.agent.approve(key)
        with self.assertRaisesRegex(KeyError, "resolved"):
            self.agent.resume(key, {"approved": True})


class OpenRouterPlannerTests(unittest.TestCase):
    """Unit-tests the adapter logic (prompt construction, schema mapping)
    with a mocked LLM client -- no network, no credentials. The live
    OpenRouter call itself is not exercised by any test in this suite; treat
    it as implemented-and-adapter-tested, not live-verified."""

    def test_maps_structured_output_into_an_llm_proposal(self) -> None:
        class FakeStructuredResponse:
            outcome = "REQUEST_EVIDENCE"
            action = "request_document"
            rationale = "Missing proof of address per KYC-EVIDENCE-07."
            confidence = 0.88

        class FakeStructuredClient:
            def invoke(self, messages):
                # The case note must be present but explicitly marked untrusted.
                user_message = messages[1][1]
                assert "untrusted" in user_message
                return {"parsed": FakeStructuredResponse(), "raw": None, "parsing_error": None}

        class FakeChatOpenAI:
            def __init__(self, **kwargs):
                pass

            def with_structured_output(self, schema, include_raw=False):
                assert include_raw
                return FakeStructuredClient()

        with patch("app.planner.os.environ", {"OPENROUTER_API_KEY": "test-key"}), \
             patch("langchain_openai.ChatOpenAI", FakeChatOpenAI):
            planner = OpenRouterPlanner(model="fake-model")
            proposal = planner.propose(
                "KYC-1042",
                {"verify_documents": {"missing_fields": ["proof_of_address"]}},
                [],
                "some untrusted case note",
            )
        self.assertEqual(proposal.outcome, "REQUEST_EVIDENCE")
        self.assertEqual(proposal.action, "request_document")
        self.assertAlmostEqual(proposal.confidence, 0.88)
        self.assertEqual(proposal.model, "openrouter:fake-model")


class I18nTests(unittest.TestCase):
    def test_render_reason_covers_every_policy_branch(self) -> None:
        for key, params in [
            ("sanctions_hit", {"score": 0.9, "threshold": 0.8}),
            ("identity_conflict", {}),
            ("missing_evidence", {"fields": ["proof_of_address"]}),
            ("clear", {}),
        ]:
            self.assertTrue(i18n.render_reason(key, params, "en"))
            self.assertTrue(i18n.render_reason(key, params, "vi"))

    def test_unsupported_language_falls_back_to_english(self) -> None:
        en = i18n.render_reason("clear", {}, "en")
        unknown = i18n.render_reason("clear", {}, "fr")
        self.assertEqual(en, unknown)

    def test_translate_via_llm_returns_none_without_credentials(self) -> None:
        with patch("app.i18n.os.getenv", return_value=""):
            self.assertIsNone(i18n.translate_via_llm("hello", "vi"))


if __name__ == "__main__":
    unittest.main()
