"""Zero-dependency HTTP demo: no extra process, no external services.

This is the recommended surface for a live interview -- it starts in under a
second and never depends on network access. `app/ui.py` (Streamlit) is the
optional, richer surface for showing LangGraph's native interrupt/resume UI
and MLflow tracing side by side with a real OpenRouter model.

One `KYCExceptionAgent` is shared by every request in this process. Planner
and language are per-request fields, not separate agent instances, so
approving a run is never ambiguous regardless of which planner or language
produced it (see `agent.py`'s docstring).
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .agent import KYCExceptionAgent
from .tools import DomainTools

ROOT = Path(__file__).resolve().parents[1]
TOOLS = DomainTools()
AGENT = KYCExceptionAgent(tools=TOOLS)


class Handler(BaseHTTPRequestHandler):
    def _json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/cases":
            return self._json(TOOLS.list_cases())
        if path in {"/", "/index.html"}:
            body = (ROOT / "static" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        self.send_error(404)

    def do_POST(self) -> None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size) or b"{}")
            if self.path == "/api/run":
                decision = AGENT.run(
                    str(payload["case_id"]),
                    planner_name=payload.get("planner"),
                    lang=str(payload.get("lang", "en")),
                )
                return self._json(decision.to_dict())
            if self.path == "/api/approve":
                key = str(payload["approval_key"])
                lang = payload.get("lang")
                decision = AGENT.approve(key, lang=lang)
                return self._json(decision.to_dict())
            if self.path == "/api/relocalize":
                decision = AGENT.relocalize(str(payload["decision_id"]), str(payload.get("lang", "en")))
                return self._json(decision.to_dict())
            self.send_error(404)
        except KeyError as exc:
            self._json({"error": f"Unknown or missing field: {exc}"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[demo] {fmt % args}")


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("KYC Agent demo: http://localhost:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
