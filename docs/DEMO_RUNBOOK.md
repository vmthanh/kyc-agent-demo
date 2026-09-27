# Demo Runbook

Copy-paste commands for running the demo live. For talking points and timing, see
`docs/INTERVIEW_GUIDE.md`.

> CLI output is plain JSON (set `KYC_PRINT_GRAPH=1` to also print the Mermaid
> graph). The `sed -n '/^{/,$p'` filter below is harmless either way.

## Presentation files

- Main 14-slide deck (plain headlines + technical "under the hood" detail): open `slides/index.html` in a browser (`N` toggles speaker notes).
- Concise 9-slide technical deck: `slides/index-concise.html`.
- PowerPoint (generated locally, gitignored): `uv run --with python-pptx python slides/to_pptx.py`.
- Narrative/timing: `docs/INTERVIEW_GUIDE.md`. Technical detail lives in the concise deck and speaker notes.

## 0. One-time setup

```bash
cd ~/Downloads/kyc-agent-demo
cp .env.example .env        # skip if .env already exists
# edit .env: set OPENROUTER_API_KEY=...
uv sync
```

## 1. Pre-flight check (~30 s, before the interview)

```bash
uv run python -m unittest discover -s tests     # expect: Ran 199 tests ... OK
uv run python -m evals.run_evals                 # expect: 22/22 checks passed
uv run python -m evals.openrouter_smoke --case KYC-1045   # live key + network check
```

## 2. Start the demo server

```bash
uv run python -m app.api
```

- Starts in ~1 s. An MLflow "not reachable" warning is fine when no MLflow server is running.
- Open **http://localhost:8000**.
- Health check: `curl -s localhost:8000/health` → `{"status":"ok",...}`.

## 3. Browser demo script

Keep **Planner = `normal (OpenRouter)`** for all four steps.

**① Plain LLM + RAG vs governed agent (KYC-1044)**
1. Select `KYC-1044 — Possible sanctions match`.
2. Click **Compare with generic RAG**. Takes ~4 s; the plain agent runs 10 times.
3. Show:
   - the red banner: the plain agent would have run `approve_application`;
   - the sample counts, e.g. `CLEAR ×3, ESCALATE ×7`;
   - the governed agent's `ESCALATE_COMPLIANCE` via `R-AML-01@1.0`.

If a batch shows 0 unsafe runs, click again and say so honestly.

**② Captured expert rule (KYC-1043)**
1. Select `KYC-1043`, then **Run agent**.
2. Show the rule card: `R-ID-EXP-01@1.0`, the **expert-captured rule** badge, and the ontology path.
3. Show the grounded fact `'Minh' vs 'Mlnh' (explainable by OCR/diacritics)`.
4. Click **Approve bounded action**. The payload contains `"rule": "R-ID-EXP-01@1.0"`.
5. Click **Submit verified evidence**. It moves to cycle 2 and ends **CLEAR**.
6. Switch the language to **Tiếng Việt** to show the same decision in Vietnamese.

**③ Showing the expert rule is bounded (KYC-1046)**
- **Run agent** on KYC-1046 (a different person). It stays `MANUAL_REVIEW` via `R-ID-01`.

Then point at **Authority 0/2 · maker-checker**: KYC-1046 is HIGH risk, so the
ontology's authority matrix requires two different approvers. Approve as
`an.nguyen (analyst)` → **1/2**, nothing executes; approve again as
`lan.pham (kyc_lead)` → ticket executed, `approved_by` lists both. Trying the
same person twice is refused.

**④ Full evidence loop (KYC-1042)**
- **Run** → **Approve** → **Submit**. It clears on cycle 2.
- Point at the governance counters (reads 8, approvals 1, writes 1).
- If the model asks for proof of address again on cycle 2, that is a real override: the stale
  case note misled the model, and the guard corrected it.

## 4. Same flows from the CLI (fallback if the browser misbehaves)

```bash
F='{outcome, status: .workflow_status, rule: .rule.id, override: (.guardrail_override != null), gov: .governance}'

# Expert rule, end to end
uv run python -m app.cli --case KYC-1043 --planner normal --approve --submit-requested-documents \
  | sed -n '/^{/,$p' | jq "$F"

# Evidence loop
uv run python -m app.cli --case KYC-1042 --planner normal --approve --submit-requested-documents \
  | sed -n '/^{/,$p' | jq "$F"

# Real name mismatch → manual review, maker-checker (two approvers from --approvers)
uv run python -m app.cli --case KYC-1046 --planner normal --approve \
  | jq '{outcome, status: .workflow_status, approved_by: .executed_action.approved_by}'

# Compromised model → still BLOCKED
uv run python -m app.cli --case KYC-1044 --planner compromised_demo | sed -n '/^{/,$p' | jq "$F"

# Vietnamese
uv run python -m app.cli --case KYC-1043 --planner normal --lang vi | sed -n '/^{/,$p' | jq -r .summary
```

Comparison via the API (server must be running):

```bash
curl -s -X POST localhost:8000/api/compare -H 'content-type: application/json' \
  -d '{"case_id":"KYC-1044","planner_mode":"normal","samples":10}' \
  | jq '{baseline: .baseline.outcome, would_execute: .baseline.would_execute,
         governed: .governed.outcome, rule: .governed.rule.id, stats: .comparison.sample_outcomes}'
```

## 5. "Rules are data" demo (optional, ~1 min)

```bash
F='{outcome, status: .workflow_status, rule: .rule.id, override: (.guardrail_override != null), gov: .governance}'

# 1. Show the rule
jq '.cognitive.rules[] | select(.id=="R-AML-01") | {id, hard_stop, params, when}' data/ontology.json

# 2. Make a relaxed copy (threshold 0.80 → 0.95); the shipped file is never touched
jq '(.cognitive.rules[] | select(.id=="R-AML-01") | .params.threshold) = 0.95' \
  data/ontology.json > /tmp/onto-relaxed.json

# 3. Run KYC-1044 against the copy → CLEAR via R-CLEAR-01, with no code change
KYC_ONTOLOGY_PATH=/tmp/onto-relaxed.json \
  uv run python -m app.cli --case KYC-1044 --planner normal | sed -n '/^{/,$p' | jq "$F"
```

Then say: "that's exactly why rule changes need a promotion gate". That gate is plan Task 7.

## 5a. Reflect & Assurance (~3 min, browser)

1. **Agent** tab → `KYC-1043` → **Run agent**.
2. Type a rejection reason, e.g. `Tamper 0.09 is borderline; borderline captures need manual review` → **Reject action**.
   A yellow hint says the reviewer signal was captured.
3. **Reflect & Assurance** tab → the signal `SIG-001` → **Draft (rule narrowing)**.
   The candidate is `R-ID-EXP-01@1.1` with `max_tamper 0.10 → 0.09`; replay: 65 cases, 1 tightened, 0 regressions.
4. **Approve** as `lan.pham (kyc_lead)` → **1/2**, still proposed; then **Approve** as
   `hoa.tran (compliance)` → **PROMOTED**, ontology **v2.1**. (An `analyst`, or a second
   `kyc_lead`, is refused: promotion needs one KYC Lead + one Compliance.)
5. Back in **Agent**, run KYC-1043 again → now `MANUAL_REVIEW` via `R-ID-01`.
6. **Try an unsafe amendment** → R-AML-01 threshold 0.95 → **BLOCKED**, 9 safety regressions (G-001…G-009 incl. KYC-1044).
7. **Reset to shipped ontology** before the next run-through.

Optional: **Draft (LLM)** asks gpt-4o-mini for the amendment; its output goes through the same validation, replay, and approval.

## 5b. Scorecard (60 labelled cases, ~35 s live)

```bash
uv run python -m evals.scorecard --planner eval                 # offline sanity, ~3 s
uv run python -m evals.scorecard --planner normal --baseline    # governed vs generic, live
```

Show the table: outcome accuracy, unsafe decisions, unsafe on injected notes, unapproved writes.

## 5c. Local model option (requires separately installed inference server)

No Ollama installation or model download is bundled with this repo. With Ollama
installed and a model pulled, start `ollama serve`, then:

```bash
LOCAL_LLM_MODEL=qwen2.5:7b-instruct uv run python -m app.cli --case KYC-1044 --planner local
uv run python -m evals.scorecard --planner local --baseline --workers 2
```

The local mode uses `LOCAL_LLM_BASE_URL` (default `http://127.0.0.1:11434/v1`),
and rejects an unreachable endpoint before the graph starts. Invalid structured
output retries, then fails closed. Do **not** claim small-model parity without
measuring valid proposals on the full golden set. Hosted 7–8B probes failed the
structured-output contract; see `docs/results/2026-09-27-small-model-probe.md`.

## 6. Optional extras

**Streamlit UI with MLflow tracing** (3 terminals):

```bash
# T1 — MLflow
mkdir -p .runtime/mlflow && uv run mlflow ui --host 127.0.0.1 --port 5001 --workers 1 \
  --backend-store-uri sqlite:///.runtime/mlflow/mlflow.db --default-artifact-root .runtime/mlflow/artifacts
# T2 — agent runtime
uv run python -m app.api
# T3 — Streamlit
KYC_API_URL=http://127.0.0.1:8000 uv run streamlit run app/ui.py
```

**Durable checkpoints (Redis Stack, not plain Redis):**

```bash
docker run -d --name kyc-redis -p 6379:6379 redis/redis-stack-server:latest
REDIS_URL=redis://localhost:6379 uv run python -m app.api
```

## 7. If something breaks on stage

| Symptom | Fix |
|---|---|
| Port 8000 already in use | `pkill -f app.api`, then restart |
| `OPENROUTER_API_KEY is required` | Set the key in `.env` and restart the server |
| OpenRouter slow or down | Say so plainly. The strict safe route is part of the story; show `uv run python -m evals.run_evals` offline |
| Screen gets cluttered | Reload the page; each run is independent |
