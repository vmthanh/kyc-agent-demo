# Task 1 report — Executable Cognitive Ontology

## What changed

- `data/ontology.json` v2.0: `structural` (entities incl. new `Rule`, relations) + `cognitive`
  (`fact_sources`, `actions` allowlist, `base_retrieval_tags`, 4 versioned rules
  `R-AML-01`, `R-ID-01`, `R-EVID-01`, `R-CLEAR-01`, each with `cites`, `source`, `ontology_path`).
- `app/ontology.py` (new): validating loader + closed-language interpreter.
  - Ops `== != >= > <= < in not_empty empty is_true is_false`; `all/any/not`.
  - `$fact.path` and `@param` references; threshold lives once in `params.threshold`.
  - Load-time rejections: unknown op/outcome/action/entity/param, fact paths outside
    grounded sources, any `case_note` reference, duplicate ids, missing/misplaced fallback,
    duplicate priorities, hard-stop with an action, hard-stop shadowed by a soft rule.
  - Missing/incomparable fact → `FactMissingError` (fail closed).
  - `KYC_ONTOLOGY_PATH` env override; cache keyed on mtime (edit file → next run picks it up).
- `app/policy.py`: no outcome branches left; `evaluate()/tags_for()` delegate to the ontology
  (optional `ontology=` injection). `PolicyVerdict` gains `rule_id`, `rule_version`, `cites`
  (defaults `None`, so old serialized verdicts still load). `SANCTIONS_THRESHOLD` kept, read from data.
- `app/workflow/nodes.py`: removed hardcoded `REQUIRED_POLICY_BY_REASON`; precheck requires the
  matched rule's `cites`; precheck trace names `rule@version (policy)`; decision carries
  `rule_id/rule_version`; action payload gains `"rule": "R-EVID-01@1.0"` (approval bound to rule
  version → new idempotency key on rule change). Planner-outage path clears rule attribution.
- `app/agent.py`: removed hardcoded `ONTOLOGY_PATH`; decision lineage from `Ontology.path_for()`,
  e.g. `Customer -> KYCApplication -> RiskFinding -> Rule R-AML-01@1.0 -> Policy AML-SCREEN-02 -> Resolution ESCALATE_COMPLIANCE`.
- Docs: README (key points, repo map, "Rules are data"), ARCHITECTURE control-plane + map.

## Verification

- `uv run python -m unittest discover -s tests` → **163 tests OK** (139 baseline + 24 new in `tests/test_ontology.py`).
- `uv run python -m evals.run_evals` → 19/19.
- Parity: 432-point grid (score × name × liveness × missing × risk level) matches the legacy
  branches for outcome, action, risk, reason, params, and retrieval tags.
- Demo check: `KYC_ONTOLOGY_PATH=<copy with threshold 0.95>` → KYC-1044 becomes `CLEAR` via
  `R-CLEAR-01`; shipped ontology → `ESCALATE_COMPLIANCE` via `R-AML-01`.

## Notes / follow-ups

- Static UI (`static/index.html`) doesn't render `ontology_path` yet (Streamlit does) — Task 3/12.
- i18n `sanctions_hit` template still hardcodes "(AML-SCREEN-02)" text; fine while the rule cites it.
- Relaxing the threshold is intentionally possible via data; Task 7's promotion gate is what
  should stop an unsafe change reaching production.
