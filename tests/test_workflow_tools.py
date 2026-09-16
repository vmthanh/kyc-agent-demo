import unittest

from app.tools import DomainTools, action_idempotency_key


class WorkflowToolTests(unittest.TestCase):
    def test_verified_submission_removes_only_the_submitted_missing_field(self) -> None:
        tools = DomainTools()
        result = tools.call(
            "verify_documents",
            "recheck submitted evidence",
            {
                "case_id": "KYC-1042",
                "submitted_documents": [{"type": "proof_of_address", "status": "verified"}],
            },
        )
        self.assertEqual(result.output["missing_fields"], [])

    def test_submission_does_not_mutate_the_shared_case_fixture(self) -> None:
        tools = DomainTools()
        tools.call("verify_documents", "run one", {
            "case_id": "KYC-1042",
            "submitted_documents": [{"type": "proof_of_address", "status": "verified"}],
        })
        fresh = tools.call("verify_documents", "run two", {"case_id": "KYC-1042"})
        self.assertEqual(fresh.output["missing_fields"], ["proof_of_address"])

    def test_idempotency_key_is_order_independent_but_payload_sensitive(self) -> None:
        first = {"case_id": "KYC-1042", "action": "request_document", "documents": ["proof_of_address"], "policy_versions": ["2026.3"]}
        reordered = {"policy_versions": ["2026.3"], "documents": ["proof_of_address"], "action": "request_document", "case_id": "KYC-1042"}
        changed = {**first, "documents": ["bank_statement"]}
        self.assertEqual(action_idempotency_key(first), action_idempotency_key(reordered))
        self.assertNotEqual(action_idempotency_key(first), action_idempotency_key(changed))
