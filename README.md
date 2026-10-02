# KYC Exception Resolution Agent

A LangGraph agent for KYC exceptions: a language model **proposes** a
resolution, a deterministic policy guardrail **verifies and can override** it,
and a human **approves** every write before it executes.

The core question: *what happens when the model is wrong, hallucinating, or
manipulated?*

- **Rules are data.** Versioned rules in `data/ontology.json` are interpreted
  by `app/ontology.py` in a closed rule language. Every verdict cites its rule
  (`R-AML-01@1.0`) and policy.
- **The guardrail is the safety boundary.** `app/policy.py` recomputes the
  mandated outcome from typed facts, never from the model's answer, and logs
  every override.
- **Prompt injection is tested live.** `KYC-1044` carries an injected case
  note; in `compromised_demo` mode the guardrail still forces
  `ESCALATE_COMPLIANCE`.
- **Writes need approval.** Each write pauses on a LangGraph `interrupt()`,
  carries its exact parameters, and runs through an idempotent action gateway.
- **English / Vietnamese** rendering per request.

Synthetic data only. No bank or customer information is included.

## Quick start

```bash
cp .env.example .env        # set OPENROUTER_API_KEY
uv sync
uv run python -m app.api    # http://localhost:8000
```

Pick a case, planner mode (`normal`, `compromised_demo`, `local`) and
language. Full demo steps and troubleshooting: `docs/DEMO_RUNBOOK.md`.

CLI:

```bash
uv run python -m app.cli --case KYC-1042 --planner normal --approve --submit-requested-documents
uv run python -m app.cli --case KYC-1044 --planner compromised_demo
```

Durable checkpoints (needs **Redis Stack**, not plain Redis, because
`RedisSaver` uses RediSearch):

```bash
docker run -d --name kyc-redis -p 6379:6379 redis/redis-stack-server:latest
REDIS_URL=redis://localhost:6379 uv run python -m app.api
```

`GET /health` shows which checkpointer is active. Without `REDIS_URL` the
runtime uses in-memory checkpoints. Pending approvals live in process memory,
so the service runs as a single replica; restart it to reset demo state.

## Architecture

| Layer | Choice |
|---|---|
| Orchestration | LangGraph: explicit state, branching, checkpointing, HITL interrupts |
| Planner | OpenRouter via `langchain-openai`, or a local OpenAI-compatible endpoint |
| Safety | `app/policy.py` + `app/ontology.py`: pure functions, independent of the model |
| Authority | `app/authority.py`: risk-tiered approvals, maker-checker, role separation |
| Reflect | `app/reflect.py`: reviewer feedback to candidate rule, replayed over golden cases, blocked on safety regression |
| API / UI | FastAPI + static UI; optional Streamlit client over HTTP |
| Observability | MLflow tracing (optional) |

```text
app/        agent, workflow graph, policy, ontology, planner, tools, API, UI
data/       ontology.json, cases.json (5 cases), policies.json, golden/ (60 labelled cases)
evals/      deterministic evals, scorecard, OpenRouter smoke test
tests/      unit, workflow, API and checkpointing tests
docs/       ARCHITECTURE, DEMO_RUNBOOK, PRODUCTION_ROADMAP, results/
slides/     HTML presentation deck (index.html)
```

More detail: `docs/ARCHITECTURE.md`.

## Tests and evaluation

```bash
uv run python -m unittest discover -s tests -v
uv run python -m evals.run_evals
uv run python -m evals.scorecard --planner eval                # offline
uv run python -m evals.scorecard --planner normal --baseline   # live, vs generic LLM + RAG
uv run python -m evals.openrouter_smoke --case KYC-1045        # live provider check
```

CI uses deterministic eval doubles and a mocked OpenRouter transport, so no
network or API key is needed.

Latest live scorecard (`docs/results/2026-09-27-scorecard-gpt-4o-mini.md`):
governed agent 100% accurate with 0 unsafe decisions; generic LLM + RAG
baseline 71.7% with 2 unsafe decisions and 48 unapproved writes.

## Optional: MLflow tracing

```bash
mkdir -p .runtime/mlflow
uv run mlflow ui --host 127.0.0.1 --port 5001 --workers 1 \
  --backend-store-uri sqlite:///.runtime/mlflow/mlflow.db \
  --default-artifact-root .runtime/mlflow/artifacts
MLFLOW_TRACKING_URI=http://127.0.0.1:5001 uv run python -m app.api
KYC_API_URL=http://127.0.0.1:8000 uv run streamlit run app/ui.py
```

Use port 5001 and `127.0.0.1`: on macOS, AirPlay Receiver holds port 5000.
A nonfatal `on_interrupt` callback warning from the MLflow tracer is expected;
traces still persist.

## Optional: local model

`--planner local` uses `LOCAL_LLM_BASE_URL` (default Ollama
`http://127.0.0.1:11434/v1`) and `LOCAL_LLM_MODEL` (default
`qwen2.5:7b-instruct`). Invalid output fails closed after bounded retries. In a
10-case probe, Qwen2.5-7B and Qwen3-8B failed the structured-output contract
(`docs/results/2026-09-27-small-model-probe.md`).

## Design notes

- **Expert rules stay bounded.** `R-ID-EXP-01` lets an OCR/diacritic-only name
  mismatch (`Trần Minh Anh` vs `Tran Mlnh Anh`) request a clearer ID instead of
  a full review (KYC-1043); KYC-1046, a different person, proves the bound.
- **Generic RAG comparison.** `POST /api/compare` runs the same case through a
  read-only prompt + RAG baseline. On KYC-1044 it cleared a 0.91 sanctions hit
  in 3 of 10 runs; the governed agent escalated every time.
- **Untrusted input stays data.** Case notes come from a separate tool and
  never reach `policy.py`.
- **Idempotency.** The gateway key hashes case, action, parameters and policy
  versions, so retries replay one ticket. Production should enforce it as a
  unique constraint in the durable store, not an in-process lock.

Production plan: `docs/PRODUCTION_ROADMAP.md`.
