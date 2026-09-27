# 15-minute interview guide — DanaOS-style KYC POC

> This is an independent **synthetic-data POC inspired by Aitomatic's public
> DanaOS concepts**, not DanaOS, a bank-approved KYC policy, or a production
> deployment. Present from **http://localhost:8000** (not optional Streamlit at
> :8501). Slide deck: `slides/index.html`; command runbook: `docs/DEMO_RUNBOOK.md`.

## Pre-flight (before the interview)

```bash
cd ~/Downloads/kyc-agent-demo
uv run python -m unittest discover -s tests       # 228 tests OK (as of 2026-09-27)
uv run python -m evals.run_evals                   # 24/24
uv run python -m evals.openrouter_smoke --case KYC-1045
uv run python -m app.api                           # http://localhost:8000
```

In the UI's **Reflect & Assurance** tab, click **Reset to shipped ontology**
before the walkthrough. Open the slide deck in a separate tab. If OpenRouter is
unavailable, show the recorded scorecard and use offline evals. No hidden model
fallback exists in the runtime.

## Timeline

| Time | Demonstrate | Message |
|---|---|---|
| 0:00–1:00 | Slides 1–5: customer problem, graph, DanaOS-style mapping | This is an analogy to their concepts, not product integration. Structural ontology says what exists; cognitive rules say what to do. |
| 1:00–4:00 | UI **KYC-1044 → normal → Compare with generic RAG** | Same model; 10 samples. Baseline sometimes CLEAR/`would_execute=approve_application` on a 0.91 sanctions score; governed R-AML-01 escalates, no write. The baseline is **read-only**; would_execute is counterfactual. |
| 4:00–7:00 | **KYC-1043 → Run → Approve → Submit evidence**; then **KYC-1046 → Run** | Versioned expert OCR rule requests recapture, not approval. A real name mismatch still needs manual review and maker-checker (two distinct approvers). |
| 7:00–10:00 | **KYC-1042 → Run → Approve → Submit**; point at phase tags, P→V→C strip, counters | Reads are automatic; writes are governed, human approved, idempotent. A cycle-2 model request based on stale case text is a *real* guard override when observed. |
| 10:00–13:00 | **Reflect**: reject KYC-1043 (fresh run), draft rule, replay 65 cases, KYC Lead + Compliance promote, then try unsafe sanctions threshold | Evidence-led change: amendment activates only after replay and two-role approval; threshold 0.95 is BLOCKED by 9 regressions. Reset afterward. |
| 13:00–15:00 | Scorecard slide, production seams, questions | One gpt-4o-mini run: governed 100% outcome/rule agreement and 0 unsafe decisions vs generic 71.7% and 2 unsafe. Ask which customer workflow to pilot. |

## Key facts to say precisely

- **Model vs outcome:** The live model proposed the labelled outcome on 80% of
  the 60-case set; the rule guard yielded 100% outcome agreement on that set.
  This is **synthetic label agreement**, not real-world accuracy.
- **Write claim:** Baseline `unapproved_writes=48` counts **counterfactual**
  `would_execute` actions. It never calls the gateway. Governed writes require
  approval; sanctions escalation has no automated action.
- **Comparison variance:** KYC-1044 baseline cleared in 3–4 of 10 observed
  live samples, but a new sample can differ. If it yields 0/10, show the live
  result honestly; do not invent a divergence. Use `normal`, not
  `compromised_demo` for the side-by-side.
- **Captured expert:** `R-ID-EXP-01` has a synthetic expert-source attribution.
  The bounded heuristic recognizes lost Vietnamese diacritics plus OCR i/l
  confusion at low tamper and passed liveness; remedy is re-upload, never
  approval. `KYC-1046` is the negative control.
- **Reflect:** The default narrowing draft turns `max_tamper` 0.10 into 0.09
  after a rejection of KYC-1043. Replay reports one tightened shipped case,
  zero regressions. Promotion requires distinct KYC Lead and Compliance roles
  per `data/ontology.json`; UI identities are not authenticated in this POC.
- **Assumptions:** "546 analyst minutes saved of 900" is a configurable
  scenario estimate, **not observed customer productivity**. OpenRouter did not
  report per-call cost in this recorded run; do not present $0 as a measured cost.
- **Local model:** `LocalPlanner` is an adapter with mocked HTTP tests, not an
  installed or benchmarked local model. Limited hosted Qwen 7–8B probes got
  **zero valid structured proposals** in 10 cases each. No claim of small-model
  parity or air-gapped validation. `docs/results/2026-09-27-small-model-probe.md`.
- **Future work (P2):** document-to-rule curation, a second vertical pack,
  live graph/lineage UI, durable approval/audit state, tenant authorization.
  Do not describe these as implemented.

## If the live provider fails

Run `uv run python -m evals.run_evals` (24/24 expected), and show the dated
recorded `docs/results/2026-09-27-scorecard-gpt-4o-mini.md` and slide images.
Explicitly call them **recorded**, not live. The runtime's safe-failure path is
a separate capability; an eval double is not a production model fallback.

## Questions to invite

1. Which expert workflow has the most expensive repeat exception or 3 a.m.
   escalation, and who owns its policy?
2. Which action could the first pilot write, under which authority/quorum?
3. What would count as evidence that a rule amendment is safe to promote?
