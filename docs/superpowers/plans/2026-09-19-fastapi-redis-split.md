# FastAPI Runtime Split and Redis Checkpointer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the agent runtime from its frontends behind a validated FastAPI boundary, and make LangGraph checkpoints durable in Redis with an in-memory fallback.

**Architecture:** `app/api.py` (FastAPI) owns one `KYCExceptionAgent` created in a lifespan handler and shared by every request. Streamlit and the static HTML page both become clients of it. `app/checkpointing.py` picks `RedisSaver` when `REDIS_URL` is usable and `MemorySaver` otherwise, warning loudly on the degraded path.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic v2, httpx, uvicorn, LangGraph 1.2.11, `langgraph-checkpoint-redis`, unittest.

**Spec:** `docs/superpowers/specs/2026-09-19-fastapi-redis-split-design.md`

## Global Constraints

- Test runner is **unittest, not pytest**. Full suite: `uv run python -m unittest discover -s tests`. Single module: `uv run python -m unittest tests.test_checkpointing -v`.
- The suite must run with **no infrastructure**. No test may require a live Redis or a live network call.
- The API error contract is fixed: body is `{"error": "<message>"}`, status is **400** for bad requests and **404** for unknown cases and stale interrupt keys. FastAPI's default 422 `{"detail": [...]}` must never reach a client — `static/index.html` and the API tests both depend on the flat shape.
- `app/cli.py` stays in-process and must not be modified.
- Tasks 1–2 must retain all 118 existing tests. Task 3 migrates the 8 stdlib
  API tests in `tests/test_workflow_api.py` and the 2 `app.server.Handler`
  tests in `tests/test_agent.py`; the other 108 existing tests must stay green
  after every task.
- Verified facts this plan relies on, already confirmed against this codebase — do not re-litigate them:
  - `response_model=AgentDecision` serializes a key set exactly equal to `AgentDecision.__dataclass_fields__`, with `Outcome`, `WorkflowStatus` and `PendingTaskKind` rendered as their string values.
  - `TypeAdapter(AgentDecision).validate_python(body)` rehydrates nested dataclasses and returns real enum members, so `is`-comparison against `PendingTaskKind` still works.
  - `ToolError` subclasses `RuntimeError`, not `ValueError`, so the two need separate handlers.

---

### Task 1: Checkpointer selection with fallback

**Files:**
- Create: `app/checkpointing.py`
- Create: `tests/test_checkpointing.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `make_checkpointer(stack: ExitStack, redis_url: str | None = None) -> tuple[Any, str]`, returning `(saver, backend_name)` where `backend_name` is one of `"redis"`, `"memory"`, `"memory (degraded)"`. Tasks 3 and 6 consume both elements.

- [ ] **Step 1: Add the dependencies**

In `pyproject.toml`, add to the `dependencies` list, keeping it alphabetical:

```toml
    "fastapi>=0.115",
    "httpx>=0.27",
    "langgraph-checkpoint-redis>=0.1",
    "uvicorn[standard]>=0.30",
```

Then run `uv sync` and confirm it resolves.

- [ ] **Step 2: Write the failing test**

Create `tests/test_checkpointing.py`:

```python
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from langgraph.checkpoint.memory import MemorySaver

from app.checkpointing import MEMORY, MEMORY_DEGRADED, make_checkpointer


class MakeCheckpointerTests(unittest.TestCase):
    def test_blank_url_uses_memory_without_warning(self) -> None:
        with ExitStack() as stack:
            saver, backend = make_checkpointer(stack, redis_url="")
        self.assertIsInstance(saver, MemorySaver)
        self.assertEqual(backend, MEMORY)

    def test_unreachable_redis_falls_back_and_warns_about_durability(self) -> None:
        with self.assertLogs("app.checkpointing", level="WARNING") as logs:
            with ExitStack() as stack:
                saver, backend = make_checkpointer(stack, redis_url="redis://127.0.0.1:1")
        self.assertIsInstance(saver, MemorySaver)
        self.assertEqual(backend, MEMORY_DEGRADED)
        # The operator must be able to tell durability was lost, not just that
        # something failed -- this fallback is the known silent-misconfig risk.
        self.assertIn("NOT survive", "\n".join(logs.output))

    def test_unset_environment_variable_uses_memory(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with ExitStack() as stack:
                saver, backend = make_checkpointer(stack)
        self.assertEqual(backend, MEMORY)

    def test_environment_variable_is_read_when_no_argument_is_given(self) -> None:
        with patch.dict(os.environ, {"REDIS_URL": "redis://127.0.0.1:1"}, clear=True):
            with self.assertLogs("app.checkpointing", level="WARNING"):
                with ExitStack() as stack:
                    _, backend = make_checkpointer(stack)
        self.assertEqual(backend, MEMORY_DEGRADED)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run python -m unittest tests.test_checkpointing -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.checkpointing'`.

- [ ] **Step 4: Write the implementation**

Create `app/checkpointing.py`:

```python
"""Checkpointer selection: durable Redis when configured, in-memory otherwise.

`RedisSaver.from_conn_string` is a context manager whose saver is only valid
inside its block, so callers pass in an `ExitStack` that outlives the request
(the FastAPI lifespan owns one for the process).
"""
from __future__ import annotations

import logging
import os
from contextlib import ExitStack
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)

REDIS = "redis"
MEMORY = "memory"
MEMORY_DEGRADED = "memory (degraded)"


def make_checkpointer(stack: ExitStack, redis_url: str | None = None) -> tuple[Any, str]:
    """Return `(saver, backend_name)`.

    A blank or unset `REDIS_URL` selects `MemorySaver` silently -- that is the
    documented zero-infrastructure path for demos and the test suite. A URL that
    is set but unusable warns loudly and degrades, because a production deploy
    that silently runs non-durable is the known risk of having a fallback here.
    """
    url = redis_url if redis_url is not None else os.environ.get("REDIS_URL", "")
    if not url:
        return MemorySaver(), MEMORY

    try:
        from langgraph.checkpoint.redis import RedisSaver

        saver = stack.enter_context(RedisSaver.from_conn_string(url))
        saver.setup()
        return saver, REDIS
    except Exception as exc:
        logger.warning(
            "REDIS_URL=%s is set but unusable (%s: %s). Falling back to in-memory "
            "checkpoints -- graph state will NOT survive a restart. Redis Stack is "
            "required; plain Redis without RediSearch cannot back RedisSaver.",
            url,
            type(exc).__name__,
            exc,
        )
        return MemorySaver(), MEMORY_DEGRADED
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run python -m unittest tests.test_checkpointing -v`

Expected: PASS, 4 tests.

If `test_unreachable_redis_falls_back_and_warns_about_durability` hangs for more than a few seconds, redis-py is retrying. Add a connect timeout to the URL query string in the implementation (`?socket_connect_timeout=1`) rather than lengthening the test.

- [ ] **Step 6: Run the full suite**

Run: `uv run python -m unittest discover -s tests`

Expected: OK, 122 tests (118 existing + 4 new).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock app/checkpointing.py tests/test_checkpointing.py
git commit -m "feat: select a Redis or in-memory checkpointer with a loud fallback"
```

---

### Task 2: Thread a checkpointer through the agent façade

**Files:**
- Modify: `app/agent.py:21-35`
- Modify: `tests/test_agent.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly; accepts any LangGraph saver object.
- Produces: `KYCExceptionAgent(tools=None, checkpointer=None)`. Task 3 constructs the agent with both arguments.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent.py`. Add `from unittest.mock import patch` and `from langgraph.checkpoint.memory import MemorySaver` to its imports if they are not already there:

```python
class AgentCheckpointerTests(unittest.TestCase):
    def test_checkpointer_reaches_the_compiled_graph(self) -> None:
        saver = MemorySaver()
        agent = KYCExceptionAgent(checkpointer=saver)
        with patch("app.agent.build_workflow_graph") as build:
            agent._graph_for("normal", None)
        self.assertIs(build.call_args.kwargs["checkpointer"], saver)

    def test_default_agent_passes_no_checkpointer(self) -> None:
        agent = KYCExceptionAgent()
        with patch("app.agent.build_workflow_graph") as build:
            agent._graph_for("normal", None)
        self.assertIsNone(build.call_args.kwargs["checkpointer"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m unittest tests.test_agent.AgentCheckpointerTests -v`

Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'checkpointer'`.

- [ ] **Step 3: Write the implementation**

In `app/agent.py`, replace the constructor and `_graph_for`:

```python
    def __init__(self, tools: DomainTools | None = None, checkpointer: Any | None = None) -> None:
        self.tools = tools or DomainTools()
        self.checkpointer = checkpointer
        self._graphs: dict[str, Any] = {}
        self._pending_tasks: dict[str, dict[str, Any]] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def _graph_for(self, planner_name: str | None, planner: Planner | None):
        if planner is not None:
            key, resolved = f"instance:{id(planner)}", planner
        else:
            key, resolved = planner_name or "auto", select_planner(planner_name)
        if key not in self._graphs:
            self._graphs[key] = build_workflow_graph(
                self.tools, resolved, checkpointer=self.checkpointer
            )
        return self._graphs[key]
```

`build_workflow_graph` already does `checkpointer or MemorySaver()`, so passing `None` preserves today's behavior for `cli.py` and every existing test.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run python -m unittest tests.test_agent -v`

Expected: PASS, including the two new tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m unittest discover -s tests`

Expected: OK, 124 tests.

- [ ] **Step 6: Commit**

```bash
git add app/agent.py tests/test_agent.py
git commit -m "feat: let the agent facade accept a checkpointer"
```

---

### Task 3: FastAPI runtime replacing the stdlib server

**Files:**
- Create: `app/api.py`
- Delete: `app/server.py`
- Rewrite: `tests/test_workflow_api.py`
- Modify: `tests/test_agent.py` (remove its two `Handler`-specific tests and
  the `app.server` import)

**Interfaces:**
- Consumes: `make_checkpointer` (Task 1), `KYCExceptionAgent(tools=..., checkpointer=...)` (Task 2).
- Produces: `app` (a `FastAPI` instance) importable as `app.api:app`; routes `GET /health`, `GET /api/cases`, `GET /`, and `POST /api/{run,resume,approve,reject,relocalize}`. Task 4's client targets exactly these paths.

- [ ] **Step 1: Write the failing test**

Replace the entire contents of `tests/test_workflow_api.py`:

```python
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
```

Also remove `from app.server import Handler` and the `HTTPServerTests` class
from `tests/test_agent.py`. `test_unknown_case_is_not_found` above replaces its
unknown-case assertion, and `test_reject_endpoint_passes_the_review_reason_to_the_agent`
replaces its rejection-forwarding assertion. This must happen before deleting
`app/server.py`; otherwise test collection fails.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m unittest tests.test_workflow_api -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.api'`.

- [ ] **Step 3: Write the implementation**

Create `app/api.py`:

```python
"""FastAPI runtime for the KYC exception agent.

Replaces the stdlib `http.server` demo. One `KYCExceptionAgent` is owned by the
application lifespan and shared by every request, so an approval always reaches
the process that created the interrupt.

SINGLE REPLICA ONLY. `KYCExceptionAgent` keeps `_pending_tasks` and `_results`
in process memory, so a second replica behind a load balancer would receive
approvals for interrupt keys it has never seen. Redis makes graph state durable
across a restart; it does not yet make this service horizontally scalable. See
docs/superpowers/specs/2026-09-19-fastapi-redis-split-design.md.
"""
from __future__ import annotations

import logging
import os
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .agent import KYCExceptionAgent
from .checkpointing import make_checkpointer
from .domain import AgentDecision
from .tools import DomainTools, ToolError

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"

PlannerMode = Literal["normal", "compromised_demo"]


class _Body(BaseModel):
    # A typo'd field is a 400, not a silently applied default.
    model_config = ConfigDict(extra="forbid")


class RunRequest(_Body):
    case_id: str = Field(min_length=1)
    planner_mode: PlannerMode | None = None
    planner: str | None = None
    lang: str = "en"


class ResumeRequest(_Body):
    interrupt_key: str = Field(min_length=1)
    response: dict[str, Any]
    lang: str | None = None


class ApproveRequest(_Body):
    approval_key: str = Field(min_length=1)
    lang: str | None = None


class RejectRequest(_Body):
    approval_key: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    lang: str | None = None

    @field_validator("reason")
    @classmethod
    def _reason_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A rejection reason is required")
        return value


class RelocalizeRequest(_Body):
    decision_id: str = Field(min_length=1)
    lang: str = "en"


class HealthResponse(BaseModel):
    status: str
    checkpointer: str


class CaseSummary(BaseModel):
    case_id: str
    title: str
    signal: str


def _enable_mlflow_autologging() -> None:
    """Keep opt-in tracing beside the process that invokes LangGraph."""
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        logger.info("MLflow tracing disabled; set MLFLOW_TRACKING_URI to enable it")
        return
    try:
        import mlflow

        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT_NAME", "kyc-exception-agent-demo"))
        mlflow.langchain.autolog()
    except Exception as exc:  # best-effort tracing must not stop the runtime
        logger.warning("MLflow tracing disabled: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    with ExitStack() as stack:
        _enable_mlflow_autologging()
        checkpointer, backend = make_checkpointer(stack)
        tools = DomainTools()
        app.state.tools = tools
        app.state.checkpointer_backend = backend
        app.state.agent = KYCExceptionAgent(tools=tools, checkpointer=checkpointer)
        logger.info("KYC agent runtime ready (checkpointer=%s)", backend)
        yield


app = FastAPI(title="KYC Exception Agent", lifespan=lifespan)


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.exception_handler(RequestValidationError)
async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Flatten Pydantic's error list into the `{"error": "..."}` shape the
    static frontend reads. FastAPI's default 422 `{"detail": [...]}` would
    break it."""
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = f"{location}: {first['msg']}" if location else first["msg"]
    return _error(message, 400)


@app.exception_handler(ToolError)
async def _on_tool_error(request: Request, exc: ToolError) -> JSONResponse:
    return _error(str(exc), 404)


@app.exception_handler(KeyError)
async def _on_key_error(request: Request, exc: KeyError) -> JSONResponse:
    # A stale interrupt handle is a 404 so a stale UI action reads correctly;
    # a missing request field is a 400. Behavior preserved from server.py.
    message = str(exc).strip("'")
    if message.lower().startswith("unknown or already resolved"):
        return _error(message, 404)
    return _error(f"Unknown or missing field: {exc}", 400)


@app.exception_handler(ValueError)
async def _on_value_error(request: Request, exc: ValueError) -> JSONResponse:
    return _error(str(exc), 400)


@app.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    return {"status": "ok", "checkpointer": request.app.state.checkpointer_backend}


@app.get("/api/cases", response_model=list[CaseSummary])
def list_cases(request: Request) -> list[CaseSummary]:
    return request.app.state.tools.list_cases()


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(INDEX, media_type="text/html")


@app.post("/api/run", response_model=AgentDecision)
def run(request: Request, body: RunRequest):
    # `planner_mode` absent means the legacy `planner` field supplies the mode,
    # which `agent.run` then normalizes. Preserved from server.py so a legacy
    # {"planner": "heuristic"} body keeps selecting the heuristic planner.
    mode = body.planner_mode if body.planner_mode is not None else (body.planner or "normal")
    return request.app.state.agent.run(
        body.case_id, planner_name=body.planner, lang=body.lang, planner_mode=mode
    )


@app.post("/api/resume", response_model=AgentDecision)
def resume(request: Request, body: ResumeRequest):
    return request.app.state.agent.resume(body.interrupt_key, dict(body.response), lang=body.lang)


@app.post("/api/approve", response_model=AgentDecision)
def approve(request: Request, body: ApproveRequest):
    return request.app.state.agent.approve(body.approval_key, lang=body.lang)


@app.post("/api/reject", response_model=AgentDecision)
def reject(request: Request, body: RejectRequest):
    return request.app.state.agent.reject(body.approval_key, body.reason, lang=body.lang)


@app.post("/api/relocalize", response_model=AgentDecision)
def relocalize(request: Request, body: RelocalizeRequest):
    return request.app.state.agent.relocalize(body.decision_id, body.lang)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run python -m unittest tests.test_workflow_api -v`

Expected: PASS, 18 tests.

Two failure modes to expect and how to read them:
- If `TestClient(app)` raises rather than returning a 4xx, Starlette is re-raising instead of using the handler. Confirm the handler is registered for the exact exception class.
- If a 422 appears anywhere, the `RequestValidationError` handler is not being reached.

- [ ] **Step 5: Delete the stdlib server**

```bash
git rm app/server.py
```

Then confirm no Python code still imports it:

Run: `rg -n "app\.server" app tests --glob '*.py'`

Expected: no matches. Historical references in the design and implementation
plan are intentional; Task 6 removes the README's runnable-command reference.

- [ ] **Step 6: Run the full suite**

Run: `uv run python -m unittest discover -s tests`

Expected: OK, 132 tests.

- [ ] **Step 7: Verify the server actually starts and serves**

```bash
uv run python -m app.api &
sleep 3
curl -s localhost:8000/health
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/
curl -s -X POST localhost:8000/api/run -H 'Content-Type: application/json' -d '{"case_id":"bogus","planner":"heuristic"}'
kill %1
```

Expected: `{"status":"ok","checkpointer":"memory"}`, then `200`, then a 404 body `{"error":"Unknown case: bogus"}`. The legacy heuristic planner makes this check deterministic and avoids requiring `OPENROUTER_API_KEY` before the case lookup.

- [ ] **Step 8: Commit**

```bash
git add app/api.py tests/test_agent.py tests/test_workflow_api.py
git rm --cached app/server.py 2>/dev/null; true
git commit -m "feat: replace the stdlib demo server with a validated FastAPI runtime"
```

---

### Task 4: Typed HTTP client

**Files:**
- Create: `app/client.py`
- Create: `tests/test_client.py`

**Interfaces:**
- Consumes: the routes from Task 3.
- Produces: `KYCClient(base_url, timeout=60.0)` with `list_cases() -> list[dict]`, and `run`, `resume`, `approve`, `reject`, `relocalize` all returning `AgentDecision`; plus `KYCAPIError(status: int | None, message: str)`. `status` is `None` for a transport failure. Task 5 consumes all of these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_client.py`:

```python
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
        client = KYCClient("http://testserver")
        client._http = httpx.Client(
            base_url="http://testserver", transport=httpx.MockTransport(handler)
        )
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m unittest tests.test_client -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.client'`.

- [ ] **Step 3: Write the implementation**

Create `app/client.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run python -m unittest tests.test_client -v`

Expected: PASS, 7 tests.

If `test_run_posts_the_expected_body` fails on an unexpected `lang` or `planner_mode` key, the client is sending defaults the test does not expect — reconcile the test against the client, not the other way round, since the API accepts both.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m unittest discover -s tests`

Expected: OK, 139 tests.

- [ ] **Step 6: Commit**

```bash
git add app/client.py tests/test_client.py
git commit -m "feat: add a typed HTTP client that rehydrates agent decisions"
```

---

### Task 5: Point Streamlit at the API

**Files:**
- Modify: `app/ui.py:1-60`, `app/ui.py:121-151`

**Interfaces:**
- Consumes: `KYCClient`, `KYCAPIError` (Task 4).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Replace the module docstring and imports**

In `app/ui.py`, replace lines 1-21 with:

```python
"""Streamlit frontend for the KYC exception agent.

Talks to the agent runtime in `app.api` over HTTP rather than constructing an
agent in-process. Before this split each browser session built its own
`KYCExceptionAgent` with its own checkpointer, so two tabs could not see each
other's pending approvals.

Set `KYC_API_URL` if the runtime is not on http://127.0.0.1:8000.
"""
import os
import sys
from pathlib import Path

import streamlit as st

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import i18n
from app.client import KYCAPIError, KYCClient
from app.domain import PendingTaskKind
```

`DomainTools` and `KYCExceptionAgent` imports are removed. `PendingTaskKind` stays — the client returns real enum members, so the `is` comparisons keep working. Remove the existing `MLFLOW_ENABLED`/`MLFLOW_STATUS` initialization block and its sidebar status line as well: tracing is now initialized by the process that invokes LangGraph (`app.api`) only when `MLFLOW_TRACKING_URI` is configured. This keeps the test suite network-free by default.

- [ ] **Step 2: Replace the agent construction**

Replace lines 38-42 (the `tools`/`agent` session-state block) with:

```python
if "client" not in st.session_state:
    st.session_state.client = KYCClient(os.environ.get("KYC_API_URL", "http://127.0.0.1:8000"))
client: KYCClient = st.session_state.client
```

- [ ] **Step 3: Replace the six agent call sites**

Each change is a rename plus an error guard. `KYCClient` normalizes both API
errors and `httpx` transport failures into `KYCAPIError`, so no interaction can
surface an uncaught connection error.

Line 56, case listing:

```python
try:
    cases = client.list_cases()
except KYCAPIError as exc:
    st.error(f"{i18n.ui_text(lang, 'title')}: agent runtime unreachable ({exc}).")
    st.stop()
```

Line 60, run:

```python
    try:
        st.session_state.decision = client.run(case_id, planner_mode=planner_choice, lang=lang)
    except KYCAPIError as exc:
        st.error(exc.message)
```

Line 68, relocalize:

```python
    try:
        decision = client.relocalize(decision.decision_id, lang)
    except KYCAPIError as exc:
        st.error(exc.message)
        st.stop()
```

Lines 121-123, approve:

```python
                try:
                    st.session_state.decision = client.resume(
                        decision.pending_task.interrupt_key, {"approved": True}, lang=lang
                    )
                    st.rerun()
                except KYCAPIError as exc:
                    st.error(exc.message)
```

Lines 128-135, reject — the exception type changes, because the empty-reason
rejection now comes back as a 400 from the API rather than a local `ValueError`:

```python
                try:
                    st.session_state.decision = client.resume(
                        decision.pending_task.interrupt_key,
                        {"approved": False, "reason": rejection_reason},
                        lang=lang,
                    )
                    st.rerun()
                except KYCAPIError as exc:
                    st.error(exc.message)
```

Lines 140-144, document submission:

```python
                try:
                    st.session_state.decision = client.resume(
                        decision.pending_task.interrupt_key,
                        {"documents": [{"type": "proof_of_address", "status": "verified"}]},
                        lang=lang,
                    )
                    st.rerun()
                except KYCAPIError as exc:
                    st.error(exc.message)
```

Lines 149-151, operational handoff:

```python
                try:
                    st.session_state.decision = client.resume(
                        decision.pending_task.interrupt_key, {"acknowledged": True}, lang=lang
                    )
                    st.rerun()
                except KYCAPIError as exc:
                    st.error(exc.message)
```

Everything in the render body (`app/ui.py:70-178`) is untouched.

- [ ] **Step 4: Confirm no agent references remain**

Run: `grep -n "agent\.\|KYCExceptionAgent\|DomainTools\|session_state.tools" app/ui.py`

Expected: no matches.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m unittest discover -s tests`

Expected: OK, 139 tests. `app/ui.py` has no automated tests; Step 6 is its verification.

- [ ] **Step 6: Verify the UI against a running API**

This step is the real test for this task and must not be skipped — the render
body reads roughly sixty attributes off the decision, and only a live run
exercises them.

```bash
OPENROUTER_API_KEY=... uv run python -m app.api &
sleep 3
KYC_API_URL=http://127.0.0.1:8000 uv run streamlit run app/ui.py
```

In the browser, confirm each of these:
1. The case dropdown populates.
2. Running `KYC-1042` renders an outcome, rationale, facts, citations, trace and tool calls.
3. An approval case shows the approve/reject buttons; approving advances the workflow status.
4. Rejecting with an empty reason shows the error message rather than a traceback.
5. Switching the language selector after a decision re-renders it without re-running the graph.
6. Stopping the API and triggering a Streamlit rerun (for example, clicking Run) shows a runtime-unavailable error rather than a traceback.

Then stop both processes.

- [ ] **Step 7: Commit**

```bash
git add app/ui.py
git commit -m "feat: point the Streamlit frontend at the agent runtime over HTTP"
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the run commands established in Tasks 3 and 5.
- Produces: nothing.

- [ ] **Step 1: Replace the run instructions**

Find the section around `README.md:87` that documents `uv run python -m app.server` and replace that command block with:

````markdown
```bash
# agent runtime (single replica)
uv run python -m app.api
```

Open `http://localhost:8000`. Pick a case, a planner mode, and a language. The
`normal` and `compromised_demo` planner modes require `OPENROUTER_API_KEY`.

For durable checkpoints, start Redis Stack first and point the runtime at it:

```bash
docker run -d --name kyc-redis -p 6379:6379 redis/redis-stack-server:latest
REDIS_URL=redis://localhost:6379 uv run python -m app.api
```

`GET /health` reports which checkpointer is live. Without `REDIS_URL` the
runtime uses in-memory checkpoints, which is the intended zero-infrastructure
path for the demo.

**Redis Stack is required, not plain Redis.** `RedisSaver` builds RediSearch
indices; a stock Redis build (including Homebrew's) answers `FT._LIST` with
`unknown command` and the runtime will log a warning and fall back to memory.
````

Also update README's repository-tree descriptions from `server.py` to `api.py`
and from “served by app/server.py” to “served by app/api.py”; otherwise the
stale-reference check below cannot pass.

- [ ] **Step 2: Update the Streamlit instructions**

Find the `uv run streamlit run app/ui.py` command near `README.md:164` and replace it with:

````markdown
```bash
# with the runtime from the previous section already running
KYC_API_URL=http://127.0.0.1:8000 uv run streamlit run app/ui.py
```
````

- [ ] **Step 3: Document the scale boundary**

Add this immediately after the run-instructions section:

```markdown
### Scale boundary

The runtime holds pending-approval handles in process memory, so it runs as a
**single replica**. Redis makes graph state durable across a restart; it does
not make the service horizontally scalable, and a restart still orphans
in-flight approvals. Moving those handles to Redis is the next step, tracked in
`docs/superpowers/specs/2026-09-19-fastapi-redis-split-design.md`.
```

- [ ] **Step 4: Correct the MLflow tracing location**

Where README describes Streamlit as providing MLflow tracing, update it to say
that `app.api` initializes best-effort MLflow LangChain autologging because that
is where the graph now runs. Tracing is opt-in: `MLFLOW_TRACKING_URI` must be
set, which keeps default startup and the test suite network-free. Keep
`MLFLOW_EXPERIMENT_NAME` as the optional experiment-name override, but do not
imply the Streamlit client itself captures agent traces.

- [ ] **Step 5: Check for stale runnable references**

Run: `rg -n "app\.server" README.md`

Expected: no matches.

- [ ] **Step 6: Run the full suite one final time**

Run: `uv run python -m unittest discover -s tests`

Expected: OK, 139 tests.

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "docs: document the split runtime, Redis Stack, and the scale boundary"
```

---

## Self-Review

Checked against `docs/superpowers/specs/2026-09-19-fastapi-redis-split-design.md`:

| Spec section | Task |
|---|---|
| `app/api.py` routes and lifespan | Task 3 |
| Request models | Task 3 |
| Response validation | Task 3 (asserted for decisions, health, and case summaries) |
| Error contract table | Task 3 (one test per row) |
| `app/checkpointing.py` | Task 1 |
| `app/agent.py` changes | Task 2 |
| `app/client.py` | Task 4 |
| `app/ui.py` changes | Task 5 |
| Testing table | Tasks 1, 3, 4 |
| Operational notes / run commands | Task 6 |
| Dependencies | Task 1 |
| Non-goals stated in code and docs | Task 3 (module docstring), Task 6 (README) |

Test-count arithmetic across tasks: 118 existing − 10 migrated stdlib HTTP
tests = 108 untouched; +4 (Task 1) +2 (Task 2) = 114 before Task 3; +18
(Task 3) = 132; +7 (Task 4) = 139. Each task's expected count above follows
this progression.
If a count is off after a task, reconcile before continuing rather than
adjusting the next task's expectation.
