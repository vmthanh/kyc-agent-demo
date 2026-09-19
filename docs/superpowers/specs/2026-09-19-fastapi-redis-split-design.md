# FastAPI Runtime Split and Durable Checkpointer Design

Status: Approved for implementation planning

Date: 2026-09-19

Audience: Round-two interview reviewers and implementers

## Context

The agent runtime is currently reachable through three surfaces, each of which
builds its own agent:

- `app/server.py` — a stdlib `http.server` API with a module-level
  `AGENT = KYCExceptionAgent(tools=TOOLS)` singleton, hand-rolled `isinstance`
  validation, and a static `index.html` frontend.
- `app/ui.py` — Streamlit, which constructs `KYCExceptionAgent` into
  `st.session_state`. Every browser session therefore gets a *separate* agent
  with a separate checkpointer and separate pending-approval handles.
- `app/cli.py` — an in-process local tool.

`build_workflow_graph` defaults to `MemorySaver()`, so no graph state survives a
process restart. `KYCExceptionAgent._graph_for` never passes a checkpointer, so
the parameter that already exists on `build_workflow_graph` is unreachable from
the façade.

This design splits the UI from the agent runtime, puts request and response
validation on a single typed boundary, and makes graph state durable through a
Redis checkpointer with an in-memory fallback.

## Goals

1. One agent runtime, reachable over HTTP, with Streamlit as a client of it.
2. Declarative request validation and validated responses on every route.
3. Durable LangGraph checkpoints in Redis, degrading cleanly when Redis is absent.
4. The suite stays green. 110 of the 118 current tests are untouched; the 8 in
   `tests/test_workflow_api.py` are replaced by equivalents asserting the same
   behavior through `TestClient`, and the new files add to the total.

## Non-Goals

**Horizontal scale is explicitly out of scope.** `KYCExceptionAgent` keeps
`_pending_tasks` and `_results` in process memory. Consequences, stated plainly
so this is not mistaken for a finished production story:

- The API runs as **one replica**. Two replicas behind a load balancer would
  route an `/api/approve` to a process that has never seen the interrupt key.
- A restart still orphans in-flight approvals. Graph state survives in Redis,
  but the handle needed to resume it does not.

Moving those handles to Redis — or reconstructing them from the checkpointer on
demand — is the named next step, recorded in "Follow-up work" below.

Also out of scope: authentication, rate limiting, multi-tenancy, and
OpenTelemetry export. These belong to later roadmap phases.

## Architecture

Before:

```text
static/index.html ──► app/server.py ──► AGENT (module global)
app/ui.py ─────────────────────────────► KYCExceptionAgent (per Streamlit session)
app/cli.py ────────────────────────────► KYCExceptionAgent (per invocation)
```

After:

```text
static/index.html ─┐
                   ├─► app/api.py (FastAPI) ──► agent (lifespan-owned singleton)
app/client.py ─────┤                                    │
   ▲               │                                    ▼
app/ui.py ─────────┘                        RedisSaver | MemorySaver
app/cli.py ────────────────────────────────► KYCExceptionAgent (in-process, unchanged)
```

`app/cli.py` deliberately stays in-process. It is a local development tool, and
requiring a running server to use it would be a regression.

## Components

### `app/api.py` (new, replaces `app/server.py`)

A FastAPI application exposing the existing contract:

| Method | Path               | Request model       | Response         |
|--------|--------------------|---------------------|------------------|
| GET    | `/health`          | —                   | `{"status", "checkpointer"}` |
| GET    | `/api/cases`       | —                   | `list[CaseSummary]` |
| GET    | `/`, `/index.html` | —                   | `static/index.html` |
| POST   | `/api/run`         | `RunRequest`        | `AgentDecision`  |
| POST   | `/api/resume`      | `ResumeRequest`     | `AgentDecision`  |
| POST   | `/api/approve`     | `ApproveRequest`    | `AgentDecision`  |
| POST   | `/api/reject`      | `RejectRequest`     | `AgentDecision`  |
| POST   | `/api/relocalize`  | `RelocalizeRequest` | `AgentDecision`  |

`CaseSummary` is a model over what `DomainTools.list_cases` already returns:
`case_id`, `title`, `signal`.

`/health` reports which checkpointer backend is live, so a deployment can tell
durable from degraded without reading logs.

The agent and checkpointer are created in a `lifespan` context manager and
stored on `app.state`. `RedisSaver.from_conn_string` is a context manager whose
saver is only valid inside its block, so the lifespan holds it open through a
`contextlib.ExitStack` and closes it on shutdown.

### Request models

Pydantic models replace the `isinstance` ladder at `app/server.py:52-120`:

```python
PlannerMode = Literal["normal", "compromised_demo"]

class RunRequest(BaseModel):
    case_id: str = Field(min_length=1)
    planner_mode: PlannerMode = "normal"
    planner: str | None = None          # legacy field, still accepted
    lang: str = "en"

class ResumeRequest(BaseModel):
    interrupt_key: str = Field(min_length=1)
    response: dict[str, Any]
    lang: str | None = None

class ApproveRequest(BaseModel):
    approval_key: str = Field(min_length=1)
    lang: str | None = None

class RejectRequest(BaseModel):
    approval_key: str = Field(min_length=1)
    reason: str = Field(min_length=1)    # non-empty after strip
    lang: str | None = None

class RelocalizeRequest(BaseModel):
    decision_id: str = Field(min_length=1)
    lang: str = "en"
```

Two behaviors from `server.py` must be preserved exactly, because tests assert
them:

- `planner_mode` is validated as a mode **only when the key is present**. A
  legacy `{"planner": "heuristic"}` body is not treated as a mode
  (`test_legacy_heuristic_planner_is_not_treated_as_new_mode`).
- A non-string legacy `planner` is a 400, not a pass-through
  (`test_legacy_planner_must_be_a_string`).

`model_config = ConfigDict(extra="forbid")` on each model, so a typo'd field is
a 400 rather than a silent default.

### Response validation

Routes declare `response_model=AgentDecision`. FastAPI converts stdlib
dataclasses to Pydantic models natively, so the response schema derives from
`app/domain.py` with no parallel model tree to drift out of sync.

Verified on this codebase before planning: the serialized body's key set is
exactly `AgentDecision.__dataclass_fields__`, and `Outcome`, `WorkflowStatus`
and `PendingTaskKind` all serialize to their string values — matching today's
manual conversion in `AgentDecision.to_dict` byte for byte. No compatibility
shim is needed.

### Error contract (unchanged from `server.py`)

The body stays `{"error": "<message>"}` and the status codes stay as they are,
because `static/index.html` and the existing API tests depend on both.

| Condition                        | Status | Body                          |
|----------------------------------|--------|-------------------------------|
| Pydantic `RequestValidationError`| 400    | `{"error": "<first message>"}`|
| Malformed JSON body              | 400    | `{"error": "..."}`            |
| `ValueError` from the agent      | 400    | `{"error": str(exc)}`         |
| Unknown/resolved interrupt key   | 404    | `{"error": str(exc)}`         |
| `ToolError` (unknown case)       | 404    | `{"error": str(exc)}`         |

FastAPI's default 422 `{"detail": [...]}` is overridden by a
`RequestValidationError` handler. The handler flattens Pydantic's error list to
the first message so the shape stays a flat string, as the frontend expects.

### `app/checkpointing.py` (new)

```python
def make_checkpointer(stack: ExitStack) -> tuple[BaseCheckpointSaver, str]:
    """Return (saver, backend_name). Falls back to MemorySaver with a warning."""
```

Behavior:

1. Read `REDIS_URL`. If unset, return `MemorySaver()` and `"memory"` without a
   warning — that is the documented default for demos and tests.
2. If set, attempt `RedisSaver.from_conn_string(url)` entered on `stack`, then
   `saver.setup()`.
3. On any connection or setup failure, log a warning naming the URL and the
   error, and return `MemorySaver()` with backend `"memory (degraded)"`.

The warning must be unmissable in logs, since a silently non-durable production
deploy is the known risk of this fallback choice.

### `app/agent.py` changes

`KYCExceptionAgent.__init__` gains `checkpointer: BaseCheckpointSaver | None = None`,
stored and passed through `_graph_for` into `build_workflow_graph`. Default
`None` preserves today's behavior for `cli.py` and every existing test.

### `app/client.py` (new)

```python
class KYCClient:
    def __init__(self, base_url: str, timeout: float = 60.0) -> None: ...
    def list_cases(self) -> list[dict[str, Any]]: ...
    def run(self, case_id, planner_mode="normal", lang="en") -> AgentDecision: ...
    def resume(self, interrupt_key, response, lang=None) -> AgentDecision: ...
    def approve(self, approval_key, lang=None) -> AgentDecision: ...
    def reject(self, approval_key, reason, lang=None) -> AgentDecision: ...
    def relocalize(self, decision_id, lang) -> AgentDecision: ...
```

Built on `httpx.Client`. A non-2xx response raises `KYCAPIError(status, message)`
carrying the parsed `{"error": ...}` message, so the UI can render a real reason
rather than a stack trace.

**The client rehydrates responses into `AgentDecision`** via
`TypeAdapter(AgentDecision).validate_python(...)`, rather than returning raw
dicts. Verified on this codebase: the roundtrip restores nested dataclasses and
returns real enum members, so `decision.pending_task.kind is
PendingTaskKind.ACTION_APPROVAL` and `decision.workflow_status.value` keep
working unchanged.

This is what makes the UI migration small, and it is why `TypeAdapter` is worth
the dependency over `response.json()`.

### `app/ui.py` changes

Drops `from app.agent import KYCExceptionAgent` and its `DomainTools()`
construction. `st.session_state.client = KYCClient(os.environ.get("KYC_API_URL",
"http://127.0.0.1:8000"))` replaces `st.session_state.agent`. Each of the seven
call sites (`agent.run`, `agent.relocalize`, and the five `agent.resume` calls at
`app/ui.py:121-149`) becomes the corresponding client method.

Because the client returns `AgentDecision`, every attribute read in the render
body — roughly sixty sites across `app/ui.py:70-178` — is untouched. The change
is confined to the construction block and those seven call sites.

The one behavioral difference: `agent.resume` raised `ValueError` for an empty
rejection reason, caught at `app/ui.py:134`. The client raises `KYCAPIError`
instead, so that handler changes accordingly.

The UI is still exercised manually against a running API before the work is
called done.

## Testing

| File | Coverage |
|------|----------|
| `tests/test_workflow_api.py` (rewritten) | Same cases as today, via `TestClient`: missing field → 400, non-object `response` → 400, unknown planner mode → 400, non-string legacy planner → 400, array JSON body → 400, stale interrupt key → 404, missing live key → client-visible config error. Plus: `extra="forbid"` rejection, and every 2xx body conforming to the `AgentDecision` response model. |
| `tests/test_checkpointing.py` (new) | `REDIS_URL` unset → `MemorySaver`, backend `"memory"`. `REDIS_URL` set but unreachable → `MemorySaver`, backend `"memory (degraded)"`, warning emitted. No live Redis required. |
| `tests/test_client.py` (new) | Each method hits the right path with the right body, against a mocked `httpx` transport. `{"error": ...}` bodies raise `KYCAPIError` carrying status and message. |
| Existing 118 tests | Must stay green. `tests/test_agent.py` and the workflow tests construct agents directly and are unaffected by the default-`None` checkpointer. |

Redis-backed persistence is verified manually against `redis-stack-server`, not
in the automated suite — the suite must stay runnable with no infrastructure.

## Operational notes

**Local Redis needs RediSearch.** `RedisSaver.setup()` creates search indices.
The Homebrew build on this machine is Redis 8.6.1 with only the `vectorset`
module — `FT._LIST` returns `unknown command`, so `setup()` will fail against it.
Use Redis Stack:

```bash
docker run -d --name kyc-redis -p 6379:6379 redis/redis-stack-server:latest
```

Run commands after this change:

```bash
# agent runtime (one replica)
REDIS_URL=redis://localhost:6379 uv run uvicorn app.api:app --port 8000

# Streamlit frontend
KYC_API_URL=http://127.0.0.1:8000 uv run streamlit run app/ui.py

# vanilla frontend: http://localhost:8000
```

Omitting `REDIS_URL` runs in-memory, which is the intended zero-infrastructure
path for the interview demo.

## Dependencies

Added to `pyproject.toml`: `fastapi`, `uvicorn[standard]`, `httpx`,
`langgraph-checkpoint-redis`.

`streamlit` stays, as the frontend. `grandalf` stays, for
`_print_graph_mermaid`.

## Files touched

| File | Change |
|------|--------|
| `app/server.py` | Deleted |
| `app/api.py` | New — FastAPI app, request models, error handlers, lifespan |
| `app/checkpointing.py` | New — backend selection with fallback |
| `app/client.py` | New — typed HTTP client |
| `app/agent.py` | `__init__` accepts a checkpointer, threads it through `_graph_for` |
| `app/ui.py` | Talks to `KYCClient` instead of constructing an agent |
| `tests/test_workflow_api.py` | Rewritten against `TestClient` |
| `tests/test_checkpointing.py` | New |
| `tests/test_client.py` | New |
| `pyproject.toml` | Four dependencies added |
| `README.md` | Run commands, Redis Stack note, single-replica limitation |

## Follow-up work

Recorded here so the limitation above is a decision rather than an oversight:

1. Move `_pending_tasks` and `_results` to Redis with a TTL, making the API
   stateless and multi-replica.
2. Alternatively, drop those caches and reconstruct pending interrupts from the
   checkpointer on demand, which removes the duplicate state entirely.
3. Back `action_idempotency_key` with a real dedupe store, so a retried
   `execute_action` after a crash cannot double-fire a write.
4. OpenTelemetry spans per node, exporting the `trace` and `tool_calls` state
   fields to an ops dashboard.
