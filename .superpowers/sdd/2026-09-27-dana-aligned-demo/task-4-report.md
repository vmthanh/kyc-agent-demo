# Task 4 report — Generic LLM + RAG baseline, side by side

## What changed
- `app/baseline.py` (new): `BaselineRAGAgent` — reads the same four tools, pastes case file + case
  note into an ordinary prompt, keyword-overlap retrieval over policy text, takes the model's
  answer as the decision. No ontology, precheck, guard, or approval gate. Read-only: reports
  `would_execute` (CLEAR→approve_application, REQUEST_EVIDENCE→request_document,
  MANUAL_REVIEW→open_manual_review) but never calls the gateway.
  `run_samples()` samples N (≤10) in parallel; `compare_samples()` picks the first divergent
  sample as representative and reports `samples`, `sample_outcomes`, `divergent_samples`, `unsafe_samples`.
- `app/planner.py`: `OpenRouterPlanner(generic=True)` — plain assistant prompt, case note as ordinary
  context. Only used by the baseline.
- `app/api.py`: `POST /api/compare {case_id, planner_mode|planner, lang, samples}` →
  `{baseline, governed: AgentDecision, comparison}`; governed side stays resumable; flat error contract.
- `app/client.py`: `KYCClient.compare()`.
- `static/index.html`: *Compare with generic RAG* button (10 samples), verdict banner, sample stats,
  two-column panel above the normal governed view. EN + VI.

## Bug fixed along the way (pre-existing)
- Live models return the string `"null"` for "no action". The guard compared `"null"` to `None`
  and reported a **false guardrail override on every live sanctions run**. `_normalize_action()`
  in the planner fixes it; regression test added. After the fix: 0/6 false overrides.

## Measured (live, gpt-4o-mini, temperature 0, KYC-1044)
- Baseline, generic prompt: CLEAR in 3/10 (first batch), 3/10 and 4/10 in later runs → would auto-approve a 0.91 sanctions hit.
- Baseline, compromised prompt: ESCALATE 10/10 — the retrieved AML-SCREEN-02 text ("regardless of case
  notes") wins over the compromised system prompt. So `normal` is the mode to demo the comparison in.
- Governed agent: ESCALATE_COMPLIANCE via R-AML-01 every run.
- `/api/compare` with 10 samples: ~4 s end to end.

## Verification
- 197 tests OK (+12 in `tests/test_baseline.py`, +1 planner regression), evals 22/22.
- Headless Chrome screenshot of the compare view reviewed.
