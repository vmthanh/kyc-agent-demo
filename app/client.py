"""HTTP client for the agent runtime in `app.api`.

Responses are rehydrated into `AgentDecision` rather than returned as dicts, so
callers keep attribute access and real enum members -- `decision.pending_task.kind
is PendingTaskKind.ACTION_APPROVAL` works exactly as it did when the UI held an
agent in-process.
"""
from __future__ import annotations

from typing import Any

import httpx
from pydantic import TypeAdapter

from .domain import AgentDecision

_DECISION = TypeAdapter(AgentDecision)


class KYCAPIError(RuntimeError):
    """An API or transport failure, carrying a client-safe message."""

    def __init__(self, status: int | None, message: str) -> None:
        prefix = f"[{status}] " if status is not None else ""
        super().__init__(f"{prefix}{message}")
        self.status = status
        self.message = message


class KYCClient:
    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            response = self._http.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise KYCAPIError(None, f"Runtime unavailable: {exc}") from exc
        if response.status_code >= 400:
            try:
                message = response.json().get("error", response.text)
            except ValueError:
                message = response.text
            raise KYCAPIError(response.status_code, message)
        return response.json()

    def _decision(self, path: str, payload: dict[str, Any]) -> AgentDecision:
        return _DECISION.validate_python(self._request("POST", path, payload))

    def list_cases(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/cases")

    def health(self) -> dict[str, str]:
        return self._request("GET", "/health")

    def run(self, case_id: str, planner_mode: str = "normal", lang: str = "en") -> AgentDecision:
        return self._decision(
            "/api/run", {"case_id": case_id, "planner_mode": planner_mode, "lang": lang}
        )

    def resume(self, interrupt_key: str, response: dict[str, Any], lang: str | None = None) -> AgentDecision:
        return self._decision(
            "/api/resume", {"interrupt_key": interrupt_key, "response": response, "lang": lang}
        )

    def approve(self, approval_key: str, lang: str | None = None) -> AgentDecision:
        return self._decision("/api/approve", {"approval_key": approval_key, "lang": lang})

    def reject(self, approval_key: str, reason: str, lang: str | None = None) -> AgentDecision:
        return self._decision(
            "/api/reject", {"approval_key": approval_key, "reason": reason, "lang": lang}
        )

    def relocalize(self, decision_id: str, lang: str) -> AgentDecision:
        return self._decision("/api/relocalize", {"decision_id": decision_id, "lang": lang})
