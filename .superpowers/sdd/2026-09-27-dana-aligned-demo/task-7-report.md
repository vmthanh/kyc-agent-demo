# Task 7 report — Reflect loop (evidence-gated ontology change)

## What changed
- `app/reflect.py` (new):
  - `ReviewSignal` (rejection / operational handoff: case, run, rule@version, payload, reason, facts).
  - `NarrowingDrafter`: tightens one numeric bound just enough to exclude the rejected case; refuses hard-stop rules.
    `LLMDrafter`: asks an LLM for the amended rule JSON; output is untrusted and goes through the same gate.
  - `candidate_ontology()`: splices the candidate in, bumps the ontology version, validates with the loader.
  - `replay()`: re-decides all golden (60) + shipped (5) cases under current vs candidate (pure; no model, no writes):
    changed, tightened, loosened, safety regressions (less restrictive than the label, or than today on unlabelled
    cases), label agreement before/after, label updates needed.
  - `ReflectStore`: signals, amendments, approval (roles `kyc_lead`/`compliance`), promotion → writes
    `.runtime/ontology/ontology-v<version>.json`, activates it in-process, append-only `audit.jsonl`. Shipped
    `data/ontology.json` is never modified.
- `app/ontology.py`: `Ontology.raw`; `activate()`, `reset_active()`, `active_path()`; precedence
  explicit > promoted > `KYC_ONTOLOGY_PATH` > shipped.
- `app/workflow/nodes.py`: `finalize` marks REFLECT events with `metadata.review_signal`.
- `app/agent.py`: `KYCExceptionAgent.reflect`; signals captured after resume (deduped per run).
- `app/api.py`: `GET /api/ontology`, `GET /api/ontology/rules/{id}`, `POST /api/ontology/reset`,
  `GET /api/reflect/signals`, `GET|POST /api/amendments`, `POST /api/amendments/{id}/review`.
- `static/index.html`: **Reflect & Assurance** tab (ontology table, signals, draft buttons, unsafe-amendment
  button, amendment cards with diff, replay impact, changed decisions, approvals); hint after a rejection;
  `[hidden]` CSS fix.

## Demo result (browser, verified)
- Reject KYC-1043 → SIG-001 → draft `R-ID-EXP-01@1.1` (`max_tamper 0.10→0.09`) → 65 replayed, 1 tightened,
  0 regressions → approve as kyc_lead → ontology v2.1 → KYC-1043 now MANUAL_REVIEW via R-ID-01.
- Unsafe `R-AML-01` threshold 0.95 → BLOCKED, 9 regressions (8 golden sanctions cases + KYC-1044).

## Notes
- Single approver in Task 7; Task 8 replaces `_quorum_met` with the ontology's authority matrix (2 of kyc_lead + compliance).
- A tightening amendment can lower label agreement (labels encode today's spec); replay reports
  `label_updates_needed` rather than hiding it.

## Verification
- 214 tests OK (+9 `tests/test_reflect.py`), evals 22/22; headless Chrome run of the full flow, no JS errors.
