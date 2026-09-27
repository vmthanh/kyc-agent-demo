# KYC Exception Resolution Agent

An interview-ready agentic system: a LangGraph workflow where a language
model *proposes* a resolution for a KYC exception, a deterministic policy
guardrail *verifies and can override* that proposal, and a human approves
every write action before it executes. Built for the Aitomatic Senior FDE
interview, and grounded in Thanh Vo's KYC-at-scale work at Shopee and
bank-wide AI/agent platform work at Techcombank.

## Why this design

The JD asks for hands-on agent-building, not a prompt demo, in a domain
where a wrong autonomous action (approving a sanctions hit, inventing a
policy) is a compliance incident. So the system is built around one
question: **what happens when the model is wrong, hallucinating, or
actively manipulated?**

- OpenRouter proposes an outcome from grounded facts and versioned policy
  text. `normal` and `compromised_demo` are both live OpenRouter modes.
- `app/policy.py` independently recomputes the mandated outcome from the
  same typed facts -- never from the model's answer -- and overrides the
  proposal if they disagree. Every override is logged to the trace.
- The decision logic is **data, not code**: versioned rules in
  `data/ontology.json` (`cognitive.rules`) are interpreted by
  `app/ontology.py` in a closed rule language. Every verdict names its rule
  (`R-AML-01@1.0`) and cited policy, and the approval payload is bound to that
  rule version. The loader rejects rules that read `case_note`, write actions
  on hard-stop rules, and any rule that would shadow a hard stop.
- One of the four synthetic cases (`KYC-1044`) carries a prompt-injection
  attempt in its free-text case note ("ignore the screening score..."). In
  `compromised_demo`, OpenRouter receives an explicitly compromised prompt;
  the guardrail still forces `ESCALATE_COMPLIANCE`. Deterministic eval
  doubles cover the same safety property offline.
- Every write is gated behind a LangGraph `interrupt()` for human approval,
  carries the *exact* parameters being approved (e.g. which documents are
  requested, not just the action's name), and executes through an
  idempotent action gateway.
- The whole thing renders in English or Vietnamese, chosen per request, with
  zero added network dependency for anything templated.

## Dana mapping

How this POC maps to Aitomatic's DanaOS concepts. Rows marked *planned* are
tracked in `docs/superpowers/plans/2026-09-27-dana-aligned-demo.md`.

| DanaOS concept | This repo |
|---|---|
| Structural ontology | `data/ontology.json` → `structural` (entities, relations) |
| Cognitive ontology | `data/ontology.json` → `cognitive.rules`: versioned, executable rules interpreted by `app/ontology.py` |
| Propose → Verify → Commit | OpenRouter proposal → ontology guard (`app/policy.py`) → human-approved idempotent gateway |
| Dana Assurance | *planned*: golden-set replay, impact report, gated rule promotion |
| See → Think → Act → Reflect | *planned*: trace phases |
| Sovereign / small models | *planned*: local planner mode |
| Vertical packs | *planned*: `packs/kyc`, `packs/boiler` |

## Architecture and stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | LangGraph | Explicit state, branching, checkpointing, HITL interrupts |
| Planner | OpenRouter (`langchain-openai`) | Structured live proposal in `normal` or `compromised_demo`; provider failures after construction route strictly to a safe outcome |
| Safety | `app/policy.py`, pure functions | Independent of the model; the actual safety boundary, unit-testable in isolation |
| i18n | `app/i18n.py`, template-based | Deterministic content renders in English/Vietnamese offline; only free-form LLM text needs a (best-effort, gracefully-degrading) translation call |
| Observability | MLflow (optional) | Traces + experiment tracking; natural fit with the MLOps/Databricks stack already in use |
| UI | FastAPI + static UI (primary) + Streamlit over HTTP (rich/optional) | The primary demo is a validated HTTP API with a static front end; Streamlit talks to the same API over `KYCClient` and adds MLflow tracing side by side |
| Domain | Ontology (`data/ontology.json`) + typed dataclasses (`app/domain.py`) | Governance beyond a prompt/RAG-only design |

## Repository map

```text
app/
  domain.py      typed contracts: Outcome, ToolCall, PolicyCitation, LLMProposal, AgentDecision
  policy.py      deterministic policy façade -- the safety boundary, returns PolicyVerdict from the ontology
  ontology.py    executable Cognitive Ontology: validating loader + closed-language rule interpreter
  planner.py     OpenRouter planner seam with normal / compromised_demo modes
  i18n.py        presentation-layer i18n: EN/VI templates + a best-effort LLM translation fallback
  tools.py       allowlisted read tools, versioned policy retrieval, idempotent action gateway
  agent.py       KYCExceptionAgent façade over the fan-out/fan-in workflow
  api.py         primary demo: FastAPI HTTP API + static UI, lifespan-owned checkpointer, best-effort MLflow autolog
  checkpointing.py  make_checkpointer(): Redis Stack -> in-memory fallback, credential-redacted logging
  client.py      KYCClient: typed HTTP client used by app/ui.py, rehydrates AgentDecision from JSON
  ui.py          optional rich demo: Streamlit over HTTP via KYCClient, native interrupt/resume UI
  cli.py         terminal demo
data/
  ontology.json  structural layer (entities, relations) + cognitive layer (versioned, executable rules)
  cases.json     4 synthetic cases: evidence gap, identity mismatch, sanctions hit (+ injected note), clean pass
  policies.json  versioned policy chunks with citations
evals/
  run_evals.py   deterministic eval doubles + guardrail, route, and rendering regressions
static/
  index.html     demo UI served by app/api.py
tests/
  test_agent.py         policy unit tests, tool idempotency, end-to-end graph tests, i18n tests, mocked-LLM adapter test
  test_checkpointing.py make_checkpointer() Redis/memory/degraded-fallback behavior
  test_client.py        KYCClient against a mocked HTTP transport
  test_workflow_*.py    FastAPI route contracts, error mapping, graph-surface regressions
docs/
  INTERVIEW_GUIDE.md, ARCHITECTURE.md, PRODUCTION_ROADMAP.md, ROUNDS_2_AND_3.md
slides/
  index.html     the interview deck: 20 HTML slides, speaker notes, print/export modes
  to_pptx.py     renders index.html to slides/deck.pptx (Chrome + python-pptx)
```

## Run the primary demo (recommended for the interview)

One local process is enough for the HTTP demo. Live `normal` and
`compromised_demo` runs require network access and `OPENROUTER_API_KEY`; the
opt-in smoke command checks that provider connection.

```bash
cp .env.example .env
# set OPENROUTER_API_KEY
uv sync
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

### Scale boundary

The runtime holds pending-approval handles in process memory, so it runs as a
**single replica**. Redis makes graph state durable across a restart; it does
not make the service horizontally scalable, and a restart still orphans
in-flight approvals. Moving those handles to Redis is the next step, tracked in
`docs/superpowers/specs/2026-09-19-fastapi-redis-split-design.md`.

## Run the CLI demo

```bash
uv run python -m app.cli --case KYC-1042 --planner normal --approve --submit-proof-of-address
uv run python -m app.cli --case KYC-1044 --planner compromised_demo
```

## Present the deck

`slides/index.html` is the source of truth for the presentation -- open it in
any browser and present from it directly. Narrative order: problem -> why the
usual approaches fail -> architecture -> tech stack -> highlights -> benefits
and conclusion. Keys: `←`/`→` navigate, `N` speaker notes, `E` export view
(all slides stacked, print-ready), `F` fullscreen.

Export, when a file is needed instead of a browser:

```bash
# PDF handout, 960x540pt (16:9) pages -- the recommended export
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
  --no-pdf-header-footer --virtual-time-budget=3000 \
  --print-to-pdf=slides/deck.pdf slides/index.html
```

```bash
python3 slides/to_pptx.py     # -> slides/deck.pptx, 13.333x7.5in, speaker notes included
```

`to_pptx.py` embeds each slide as a 2560x1440 image, so the result is
pixel-identical to the browser but *not* text-editable in PowerPoint -- edit
`slides/index.html` and re-export. It writes `slides/deck.pptx` and leaves
`Thanh_Vo_KYC_Agent_Demo.pptx` (the older, natively-editable deck) alone.

## Run the rich demo (LangGraph interrupt UI + MLflow tracing)

```bash
cp .env.example .env   # set OPENROUTER_API_KEY for live modes
mkdir -p .runtime/mlflow
```

Terminal 1 -- MLflow server (leave running):

```bash
uv run mlflow ui --host 127.0.0.1 --port 5001 --workers 1 \
  --backend-store-uri sqlite:///.runtime/mlflow/mlflow.db \
  --default-artifact-root .runtime/mlflow/artifacts
```

Three deliberate, verified choices here:

- **Port 5001, not 5000.** `lsof -i :5000` on this machine showed macOS's
  Control Center (AirPlay Receiver) already bound to `*:5000`. `localhost`
  can resolve to that listener (403, since it rejects non-AirPlay
  requests) while `127.0.0.1` reaches the mlflow server we actually
  started (200) -- deterministic, address-dependent, not random. Port
  5001 sidesteps the collision entirely; the tracking URI is read from `.env.example` by `app.api`.
- **`--host 127.0.0.1` explicit, and use `127.0.0.1` (not `localhost`) in
  `MLFLOW_TRACKING_URI`.** Same reason: `localhost` may resolve to a
  different listener than the one the server actually bound.
- **`--workers 1`.** One process instead of Uvicorn's default four --
  simpler to reason about and debug live in a demo.

After starting the server, confirm it before trusting the UI's tracing
status: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5001/`
must print `200`.

Terminal 2 -- the agent runtime (leave running; Streamlit talks to this over HTTP):

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5001 uv run python -m app.api
```

Terminal 3 -- the app:

```bash
KYC_API_URL=http://127.0.0.1:8000 uv run streamlit run app/ui.py
```

`.runtime/` is gitignored and holds MLflow's sqlite store explicitly,
rather than relying on `mlflow ui`'s implicit default path. **Stop the
server with Ctrl-C in Terminal 1** before ever deleting or resetting that
file -- removing a sqlite database out from under a live MLflow connection
corrupts that connection and surfaces as `sqlite3.OperationalError: attempt
to write a readonly database` on every later trace write, even though the
file itself looks fine once recreated. To reset cleanly: Ctrl-C the
server, `rm -rf .runtime/mlflow`, `mkdir -p .runtime/mlflow` again, restart.

`app.api` initializes best-effort MLflow LangChain autologging when
`MLFLOW_TRACKING_URI` is set, which keeps default startup and the test suite
network-free. Tracing is opt-in: `MLFLOW_EXPERIMENT_NAME` can be used to
override the experiment name. **Known nonfatal warning:** `mlflow.langchain.autolog()` logs `Error in
MlflowLangchainTracer.on_interrupt callback: AttributeError(...)` when the
graph pauses on a LangGraph `interrupt()` -- this mlflow version's tracer
doesn't implement that callback yet, so that callback's data is lost, but
the trace itself still persists (verified: `agent.run()` through autolog
appended a `trace_info` row with `status='OK'`, `200` on `POST /v1/traces`
and `POST /api/3.0/mlflow/traces`).

The live modes require a usable `OPENROUTER_API_KEY`; missing credentials are
rejected at planner selection as a client-visible configuration error. Once a
planner is constructed, provider failures are recorded by the graph and route
to a strict safe outcome; it never silently changes the planner. Deterministic eval doubles are available
for CI and offline regression checks.

## Run tests and evaluation

```bash
uv run python -m unittest discover -s tests -v
uv run python -m evals.run_evals
```

139 unit tests (policy branch coverage, graph routes, tool idempotency,
planner-failure resilience, end-to-end runs, i18n rendering, the mocked
OpenRouter adapter, relocalization, concurrency, FastAPI route/error
contracts, the Redis/memory checkpointer fallback, and the HTTP client) and
19 deterministic scenario/safety checks, including the compromised-proposal
guardrail route and Vietnamese rendering.

**Honest scope note:** CI uses deterministic eval doubles and a mocked
OpenRouter transport so tests do not require a network or credential. A live
preflight is opt-in:

```bash
uv run python -m evals.openrouter_smoke --case KYC-1045
```

Treat that command's result as the live provider check for the interview.

## Language switch (English / Vietnamese)

Every UI (`static/index.html`, `app/ui.py`) has a language selector, and the
CLI and HTTP API take a `lang` field. Two different rendering strategies,
by content type:

- **Deterministic content** -- outcome/risk labels, the four policy-verdict
  reasons, the guardrail override sentence, eval-double rationale, and policy
  citation excerpts -- is hand-templated in
  `app/i18n.py` and renders instantly, offline, for both languages. This is
  exhaustively covered by `I18nTests` and the Vietnamese-rendering eval.
- **Free-form content** -- a real `OpenRouterPlanner`'s rationale -- cannot
  be templated. Requesting Vietnamese triggers one best-effort translation
  call through the same OpenRouter client; on any failure (no key, timeout,
  network error) it falls back to the English original and sets
  `rationale_translated: false` so the UI can say so, instead of silently
  mistranslating or crashing.
- Switching the language selector **after** a decision already exists
  re-renders that same decision in place (`KYCExceptionAgent.relocalize`,
  the `/api/relocalize` endpoint) instead of leaving stale content on
  screen or silently re-running the graph and losing an in-flight
  approval. `test_relocalize_switches_language_of_an_existing_decision_in_place`
  pins this.
- Trace step titles/details stay English by design -- they are an
  operational audit log, not analyst-facing prose, matching how production
  audit trails are conventionally kept in one language regardless of UI
  locale.

## Design choices

- **Rules are data.** Change a rule in `data/ontology.json` (or point
  `KYC_ONTOLOGY_PATH` at an alternate file) and the next run uses it; the file
  is re-read when its mtime changes. For example, raising `R-AML-01`'s
  `params.threshold` to `0.95` turns KYC-1044 into `CLEAR` via `R-CLEAR-01`,
  with no code change. `tests/test_ontology.py` holds the safety invariants
  (the sanctions hard stop across a grid of inputs) and parity with the
  original hand-coded branches.
- **The guardrail is the product, not the model.** `policy.py` is pure,
  has no dependency on `planner.py` or `i18n.py`, and is exhaustively unit
  tested. Swapping the OpenRouter model or prompt mode never changes the
  safety boundary.
- **One agent, shared.** `KYCExceptionAgent` is meant to be a single
  instance per process (one in the HTTP server, one per Streamlit session).
  Planner and language are arguments to `run()`/`approve()`, not part of
  construction, and each run's interrupt gets a globally-unique approval
  handle (LangGraph's own interrupt id) instead of a payload-derived gateway
  key. The gateway key hashes the canonical case, action, requested fields,
  and policy versions. An earlier revision kept one `KYCExceptionAgent` per
  planner choice; that made approving a run ambiguous whenever two planners
  touched the same payload. `test_switching_planner_between_runs_does_not_break_approval`
  and `test_two_runs_of_the_same_case_get_independent_approval_handles`
  pin the fix.
- **Thread-safe under `ThreadingHTTPServer`.** The shared agent uses one
  coarse `RLock` around `run()`/`approve()`/`relocalize()`/graph-cache
  construction, and `DomainTools` locks the idempotency check-through-insert
  in `execute_approved_action`. Coarse locking serializes requests rather
  than locking per-thread_id -- the right tradeoff for a demo (correctness
  over throughput), and an explicit item for a production port to revisit.
  To be precise about what each piece of evidence shows: a throwaway
  standalone script (not part of this repo) reproduced the same unlocked
  check-and-write pattern and reliably minted 2 tickets from 20 concurrent
  calls, confirming the race is real; `test_concurrent_approvals_with_the_same_key_never_double_execute`
  and `test_concurrent_approve_calls_on_the_same_key_produce_one_ticket`
  exercise the actual, *locked* code path and pin the correct behavior
  (every worker thread's completion and ticket id is asserted, so a
  swallowed exception can't masquerade as a pass); a manual `curl` run
  fired 15 truly concurrent HTTP requests at the live server as a final
  end-to-end check. In production, don't stop at an in-process lock:
  make the idempotency key a unique constraint (or a compare-and-swap) in
  the durable store backing the action gateway, since a real deployment
  will run more than one process/replica and an in-process `Lock` only
  protects a single one.
- **Approving a proposal means approving its parameters.** The interrupt
  payload and the action gateway both carry the full `action_payload`
  (case id, action, and action-specific fields like the requested document
  list or the review evidence bundle) -- not just an action name. A
  reviewer clicking "approve" is approving a specific request, and the
  resulting ticket echoes it (`details` in the executed-action result).
- **Confidence belongs to the proposal, not the enforced outcome.**
  `proposal_confidence` is always the planner's self-reported confidence in
  *its own* (possibly rejected) recommendation. When the guardrail
  overrides the proposal, the UI shows that confidence explicitly labeled
  "(overridden)" next to the rationale it belongs to -- never next to the
  enforced outcome, which is a deterministic rule match, not a probability.
- **Untrusted input is never elevated to instruction.** Case notes are
  fetched through a separate `get_case_note` tool, kept out of
  `policy.py` entirely, and the planner's system prompt explicitly frames
  them as data. `KYC-1044`'s case note carries a live prompt-injection
  attempt to make this a real, running test rather than a claim.
- **Strict live planner behavior.** `normal` and `compromised_demo` both use
  OpenRouter. Missing credentials fail at planner selection; timeouts,
  malformed responses, and retry exhaustion after construction become explicit
  workflow state and route safely. The eval double is a test seam, not a
  runtime fallback. `.env` is loaded once via
  `python-dotenv` at `app/__init__` import.
- **Graph state is plain JSON.** Trace, citations, tool calls, and the
  decision are stored as dicts/strings inside the LangGraph state and only
  converted to typed, localized dataclasses at the boundary
  (`agent.py:_to_decision`). This keeps the state checkpoint-safe today and
  portable to a persistent checkpointer (Postgres/Redis) without a rewrite,
  and keeps every graph node language-agnostic except `review` (which needs
  a human-facing prompt at the moment it pauses).
- **Read/write separation.** Read-only tools run automatically inside the
  graph. Every write pauses on a LangGraph `interrupt()` and executes only
  after approval, through an idempotency-keyed action gateway. The key is
  payload- and policy-version-sensitive, deliberately independent of the
  per-run approval handle, so identical retries replay one ticket while a
  materially changed request gets a distinct key.
- **Synthetic data only.** No bank or customer information is included.

## Production seam

`select_planner()` in `app/planner.py` is the only place to add a new
model backend -- it must return an `LLMProposal`, nothing more. Put the
real call behind a gateway with retries/circuit breakers, move `policies.json`
into a versioned hybrid-retrieval service with jurisdiction/effective-date
filters (including per-locale translated text, not the hardcoded dict in
`i18n.py`), replace `MemorySaver` with a persistent LangGraph checkpointer,
and map trace events to OpenTelemetry spans. See `docs/PRODUCTION_ROADMAP.md`
for the phased plan.

## Demo reset

State (pending approvals, the idempotent action log, LangGraph checkpoints)
lives in process memory. Restart the server/CLI process to reset.
