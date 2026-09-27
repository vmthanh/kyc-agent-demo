from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from .domain import PolicyCitation, ToolCall
from .names import name_diff


ROOT = Path(__file__).resolve().parents[1]


class ToolError(RuntimeError):
    pass


class TransientToolError(ToolError):
    """A retryable failure from an authoritative domain tool."""


def action_idempotency_key(action_payload: dict[str, Any]) -> str:
    """Return a stable, payload-sensitive key for an approved action."""
    canonical = json.dumps(action_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class DomainTools:
    """Typed adapters. In production these wrap customer APIs and policy services."""

    def __init__(self, cases_path: str | Path | None = None) -> None:
        # `cases_path` lets evals point the same adapters at the labelled golden set.
        self.cases = json.loads(Path(cases_path).read_text()) if cases_path else self._load("cases.json")
        self.policies = self._load("policies.json")
        self.action_log: dict[str, dict[str, Any]] = {}
        self._action_lock = threading.Lock()

    @staticmethod
    def _load(name: str) -> Any:
        return json.loads((ROOT / "data" / name).read_text())

    def list_cases(self) -> list[dict[str, Any]]:
        return [
            {"case_id": c["case_id"], "title": c["title"], "signal": c["signal"]}
            for c in self.cases
        ]

    def _case(self, case_id: str) -> dict[str, Any]:
        for case in self.cases:
            if case["case_id"] == case_id:
                return case
        raise ToolError(f"Unknown case: {case_id}")

    def call(self, name: str, purpose: str, payload: dict[str, Any]) -> ToolCall:
        allowed: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "get_case": self._get_case,
            "verify_documents": self._verify_documents,
            "screen_sanctions": self._screen_sanctions,
            "get_risk_profile": self._get_risk_profile,
        }
        if name not in allowed:
            raise ToolError(f"Tool is not allowlisted: {name}")
        start = perf_counter()
        output = allowed[name](payload)
        elapsed = max(1, int((perf_counter() - start) * 1000))
        return ToolCall(name, purpose, payload, output, elapsed)

    def _get_case(self, p: dict[str, Any]) -> dict[str, Any]:
        c = self._case(str(p["case_id"]))
        return {
            "case_id": c["case_id"], "customer_segment": c["customer_segment"],
            "country": c["country"], "application": c["application"],
            "declared_name": c["declared_name"], "document_name": c["document_name"],
        }

    def _verify_documents(self, p: dict[str, Any]) -> dict[str, Any]:
        c = self._case(str(p["case_id"]))
        result = dict(c["document_verification"])
        verified = {
            str(item["type"])
            for item in p.get("submitted_documents", [])
            if item.get("status") == "verified"
        }
        result["missing_fields"] = [field for field in result["missing_fields"] if field not in verified]
        document_name = c["document_name"]
        if "id_document_reupload" in verified:
            # Synthetic stand-in for re-running OCR on a clearer capture: the
            # verified re-upload reads the declared name correctly.
            document_name = c["declared_name"]
            result["name_match"] = True
        result["name_diff"] = name_diff(c["declared_name"], document_name)
        return result

    def _screen_sanctions(self, p: dict[str, Any]) -> dict[str, Any]:
        c = self._case(str(p["case_id"]))
        return c["sanctions"]

    def _get_risk_profile(self, p: dict[str, Any]) -> dict[str, Any]:
        c = self._case(str(p["case_id"]))
        return c["risk_profile"]

    def get_case_note(self, case_id: str) -> str:
        """Untrusted free text attached to the case. Never fed to `policy.py`;
        only ever shown to a planner as data, never as an instruction."""
        return self._case(case_id).get("case_note", "")

    def retrieve_policy(self, tags: set[str]) -> list[PolicyCitation]:
        scored = []
        for policy in self.policies:
            overlap = tags.intersection(policy["tags"])
            score = len(overlap - {"kyc"})
            if score:
                scored.append((score, policy))
        scored.sort(key=lambda item: (-item[0], item[1]["policy_id"]))
        return [
            PolicyCitation(p["policy_id"], p["version"], p["section"], p["text"])
            for _, p in scored[:3]
        ]

    def execute_approved_action(self, action: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        """`action` is the full bounded-action payload a reviewer approved
        (case_id, action name, and action-specific parameters such as the
        requested documents) -- not just the action name. Everything beyond
        `action` is echoed into `details` so the resulting ticket reflects
        exactly what was approved.

        Locked end-to-end: `ThreadingHTTPServer` serves concurrent requests,
        and two callers racing on the same `idempotency_key` must never both
        pass the "not yet executed" check and mint two tickets."""
        with self._action_lock:
            if idempotency_key in self.action_log:
                return {**self.action_log[idempotency_key], "replayed": True}
            name = action.get("action")
            if name not in {"request_document", "open_manual_review"}:
                raise ToolError("Write action is not allowlisted")
            result = {
                "status": "executed",
                "action": name,
                "ticket_id": f"OPS-{len(self.action_log) + 7001}",
                "replayed": False,
                "details": {k: v for k, v in action.items() if k != "action"},
            }
            self.action_log[idempotency_key] = result
            return result
