"""Zero-dependency local HTTP demo: one process, no second local service.

This is the recommended surface for a live interview -- it starts in under a
second. Its live `normal` and `compromised_demo` planner modes do require
network access and an ``OPENROUTER_API_KEY``; deterministic eval doubles keep
offline tests network-free. `app/ui.py` (Streamlit) is the optional, richer
surface for showing LangGraph's native interrupt/resume UI and MLflow tracing
side by side with a real OpenRouter model.

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
from .tools import DomainTools, ToolError

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
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            if self.path == "/api/run":
                explicit_mode = "planner_mode" in payload
                planner_mode = payload.get("planner_mode") if explicit_mode else payload.get("planner", "normal")
                if explicit_mode:
                    if not isinstance(planner_mode, str):
                        raise ValueError("planner_mode must be a string")
                    if planner_mode not in {"normal", "compromised_demo"}:
                        raise ValueError(f"Unknown planner mode: {planner_mode}")
                elif not isinstance(planner_mode, str):
                    raise ValueError("planner must be a string")
                lang = payload.get("lang", "en")
                if not isinstance(lang, str):
                    raise ValueError("lang must be a string")
                decision = AGENT.run(
                    str(payload["case_id"]),
                    planner_name=payload.get("planner"),
                    lang=lang,
                    planner_mode=planner_mode,
                )
                return self._json(decision.to_dict())
            if self.path == "/api/resume":
                interrupt_key = str(payload["interrupt_key"])
                response = payload["response"]
                if not isinstance(response, dict):
                    raise ValueError("response must be an object")
                lang = payload.get("lang")
                if lang is not None and not isinstance(lang, str):
                    raise ValueError("lang must be a string")
                decision = AGENT.resume(interrupt_key, dict(response), lang=lang)
                return self._json(decision.to_dict())
            if self.path == "/api/approve":
                key = str(payload["approval_key"])
                lang = payload.get("lang")
                if lang is not None and not isinstance(lang, str):
                    raise ValueError("lang must be a string")
                decision = AGENT.approve(key, lang=lang)
                return self._json(decision.to_dict())
            if self.path == "/api/reject":
                key = str(payload["approval_key"])
                reason = str(payload["reason"])
                lang = payload.get("lang")
                if lang is not None and not isinstance(lang, str):
                    raise ValueError("lang must be a string")
                decision = AGENT.reject(key, reason, lang=lang)
                return self._json(decision.to_dict())
            if self.path == "/api/relocalize":
                lang = payload.get("lang", "en")
                if not isinstance(lang, str):
                    raise ValueError("lang must be a string")
                decision = AGENT.relocalize(str(payload["decision_id"]), lang)
                return self._json(decision.to_dict())
            self.send_error(404)
        except KeyError as exc:
            # Missing request fields are client errors; an unknown interrupt
            # key is also deliberately visible as a 404 for stale UI actions.
            message = str(exc).strip("'")
            # Agent-raised stale handles are 404; request-shape omissions are 400.
            status = 404 if message.lower().startswith("unknown or already resolved") else 400
            self._json({"error": message if status == 404 else f"Unknown or missing field: {exc}"}, status)
        except ToolError as exc:
            self._json({"error": str(exc)}, 404)
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
