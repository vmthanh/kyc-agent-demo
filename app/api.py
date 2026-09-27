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
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .agent import KYCExceptionAgent
from .baseline import MAX_SAMPLES, BaselineRAGAgent, compare_samples, run_samples, select_baseline_planner
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


class CompareRequest(RunRequest):
    samples: int = Field(default=1, ge=1, le=MAX_SAMPLES)


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


class CompareResponse(BaseModel):
    baseline: dict[str, Any]
    governed: AgentDecision
    comparison: dict[str, Any]


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
    if tracking_uri.startswith(("http://", "https://")) and not _tracking_server_reachable(tracking_uri):
        # mlflow.set_experiment() retries for minutes against a dead server and
        # blocks startup; tracing is best-effort, so skip it loudly instead.
        logger.warning("MLflow tracing disabled: tracking server %s is not reachable", tracking_uri)
        return
    try:
        import mlflow

        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT_NAME", "kyc-exception-agent-demo"))
        mlflow.langchain.autolog()
    except Exception as exc:  # best-effort tracing must not stop the runtime
        logger.warning("MLflow tracing disabled: %s", exc)


def _tracking_server_reachable(uri: str, timeout: float = 1.5) -> bool:
    import httpx

    try:
        httpx.get(uri, timeout=timeout)
        return True
    except httpx.HTTPError:
        return False


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
    if message.lower().startswith(("unknown or already resolved", "unknown or expired")):
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


@app.post("/api/compare", response_model=CompareResponse)
def compare(request: Request, body: CompareRequest):
    """Run the same case through a generic LLM + RAG baseline (read-only, no
    guard, no approval gate) and the governed agent, side by side. The governed
    run is a normal run: its pending approval stays resumable via /api/resume."""
    mode = body.planner_mode if body.planner_mode is not None else (body.planner or "normal")
    baseline_planner = select_baseline_planner(mode)  # fails fast (400) on a missing key
    baseline_agent = BaselineRAGAgent(request.app.state.tools)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_baseline = pool.submit(run_samples, baseline_agent, body.case_id, baseline_planner, body.samples)
        governed = request.app.state.agent.run(
            body.case_id, planner_name=body.planner, lang=body.lang, planner_mode=mode
        )
        samples = pending_baseline.result()
    baseline, comparison = compare_samples(samples, governed.outcome.value, governed.rule)
    return {"baseline": baseline, "governed": governed, "comparison": comparison}


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
