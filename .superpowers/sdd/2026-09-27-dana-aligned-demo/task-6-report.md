# Task 6 report — Golden set and scorecard

## What changed
- `evals/generate_cases.py` (new): seeded generator → `data/golden/cases.json`, 60 cases across 12
  categories (sanctions incl. exactly 0.80; near-threshold 0.70–0.7999; OCR diacritics/glyph;
  OCR-but-tampered/liveness/missing; non-OCR single token; different person; liveness; missing
  evidence; clean). 10 injected notes (EN, VI, JSON-shaped, VIP pressure, fake typo confirmation,
  fake "documents emailed"). Labels come from category specs, not the interpreter. `--check` flag.
- `evals/scorecard.py` (new): governed vs `--baseline` on the same model; metrics: outcome, rule-id,
  model-only proposal accuracy; unsafe decisions (less restrictive than label); unsafe on injected
  notes; unapproved writes; override and straight-through rates; planner failures; latency p50/p95;
  tokens/cost; assumed analyst minutes (assumptions printed with every report). JSON to `evals/out/`.
- `app/tools.py`: `DomainTools(cases_path=None)`.
- `app/domain.py` / `app/agent.py`: `AgentDecision.proposal_outcome/proposal_action` (model's own answer).
- `app/workflow/graph.py`: Mermaid printing is now opt-in (`KYC_PRINT_GRAPH=1`); CLI output is clean JSON.
  `graph_mermaid(graph)` returns the string (reused in Task 12).

## Results
- Offline (`eval`): 100% outcome/rule accuracy, 0 unsafe — interpreter agrees with all 60 spec labels.
- Offline (`eval_compromised --baseline`): governed 0 unsafe; baseline 45 unsafe, 10/10 injected.
- **Live gpt-4o-mini (`normal --baseline`)**, 34 s: governed 100% / 0 unsafe / 0 unapproved writes,
  model-alone 80% (guard overrode 20%); baseline 71.7% / 2 unsafe (1 sanctions, 1 missing-evidence) /
  48 unapproved writes; baseline 2/10 on OCR cases (no expert rule → manual review), 0/5 near-threshold
  (over-escalates). Snapshot: `docs/results/2026-09-27-scorecard-gpt-4o-mini.md`.
- OpenRouter returned no per-call cost for gpt-4o-mini → cost column 0.0.

## Verification
- 205 tests OK (+6 `tests/test_scorecard.py`), evals 22/22.
