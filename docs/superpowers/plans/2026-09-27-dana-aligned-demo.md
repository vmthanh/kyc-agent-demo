# Dana-Aligned Demo Upgrade — Implementation Plan

> **For agentic workers:** Execute task-by-task. Steps use checkbox (`- [ ]`) syntax. Commit after every task.

**Goal:** Reframe the KYC POC as a small, honest instance of Aitomatic's DanaOS thesis:
an **executable Cognitive Ontology** (not hardcoded rules), **captured expert judgment**,
**governed Propose → Verify → Commit**, and an **evidence-led Reflect loop** — with
measurable results.

**Why:** Aitomatic sells Dana Factory (build ontology) → Dana Runtime (governed agents) →
Dana Assurance (evidence-gated change). Today `data/ontology.json` is never loaded; rules
live as Python branches in `app/policy.py`. The core product idea is missing from the demo.

**Stack:** unchanged (Python 3.10+, LangGraph 1.2.11, FastAPI, unittest). No new infra.

## Global constraints

- Test runner is **unittest**: `uv run python -m unittest discover -s tests`. Baseline: **139 tests, OK** (2026-09-27).
- Every task keeps the full suite green. Tests needing no network/Redis stay that way.
- **Safety invariants never regress** (enforced by a new test in Task 1):
  - sanctions ≥ threshold → `ESCALATE_COMPLIANCE`, no action, regardless of planner/case note;
  - no write without human approval + idempotency key;
  - `case_note` is never an input to rule evaluation.
- API error contract (`{"error": ...}`, 400/404) and `AgentDecision` field set are additive-only.
- `PolicyVerdict(outcome, action, risk_level, reason_key, reason_params)` stays the interface
  consumed by nodes, i18n, and planners. New fields are optional with defaults.
- Keep `evaluate()` / `guard()` / `tags_for()` signatures so `evals/planners.py` and tests keep working.

## Phasing

| Phase | Tasks | Outcome | Est. |
|---|---|---|---|
| P0 — must have | 0–5 | Ontology drives rules; expert rule; Dana vocabulary; RAG-vs-governed compare | ~2 days |
| P1 — differentiators | 6–9 | Golden set + scorecard; Reflect loop; authority matrix; local small model | ~2–3 days |
| P2 — FDE pitch | 10–12 | Runtime/pack split + 2nd pack; curate-from-document; UI lineage/graph | ~2–3 days |
| Wrap | 13 | Docs, deck, interview guide rehearsal | ~0.5 day |

Stop after any phase and the demo still works end-to-end.

---

## P0

### Task 0: Housekeeping

**Files:** `docs/INTERVIEW_GUIDE.md`, `README.md`

- [x] Replace stale `uv run python -m app.server` with `uv run python -m app.api` in `INTERVIEW_GUIDE.md`.
- [x] Add a "Dana mapping" table stub to README (filled in Task 13).
- [x] Commit: `docs: fix stale server entrypoint in interview guide`.

### Task 1: Executable Cognitive Ontology (rules as data)

**Files:**
- Modify: `data/ontology.json` → split into `structural` (entities/relations, existing) and `cognitive.rules`
- Create: `app/ontology.py` (loader, schema validation, rule interpreter)
- Modify: `app/policy.py` (becomes a thin façade over `app/ontology.py`)
- Modify: `app/workflow/nodes.py` (`REQUIRED_POLICY_BY_REASON` derived from rules)
- Create: `tests/test_ontology.py`

**Rule schema** (JSON, no `eval`, closed operator set):

```json
{
  "id": "R-AML-01",
  "version": "2026.4",
  "priority": 10,
  "when": {"all": [{"fact": "screen_sanctions.match_score", "op": ">=", "value": 0.80}]},
  "then": {"outcome": "ESCALATE_COMPLIANCE", "action": null, "risk_level": "CRITICAL",
           "reason_key": "sanctions_hit",
           "reason_params": {"score": "$screen_sanctions.match_score", "threshold": 0.80}},
  "cites": "AML-SCREEN-02",
  "retrieval_tags": ["sanctions"],
  "hard_stop": true,
  "source": {"kind": "policy", "ref": "AML-SCREEN-02 §2.3"},
  "status": "active"
}
```

- Operators: `== != >= > <= < in not_empty is_true is_false`; combinators `all`/`any`/`not`.
- Fact paths are dotted into the four grounded fact sources only (`get_case`, `verify_documents`,
  `screen_sanctions`, `get_risk_profile`). The loader **rejects** any path rooted at `case_note`.
- `$path` in `reason_params` resolves from facts; `risk_level: "$get_risk_profile.level"` supported.
- Evaluation: active rules sorted by `priority` ascending, first match wins; a mandatory
  fallback rule (`R-CLEAR-01`, `when: {"all": []}`) must exist or the loader raises.
- `PolicyVerdict` gains optional `rule_id: str | None = None`, `rule_version: str | None = None`.

**Steps:**
- [x] Write failing `tests/test_ontology.py`:
  - loader rejects: unknown op, `case_note.*` path, missing fallback, duplicate rule id, unknown outcome;
  - interpreter reproduces current `evaluate()` for all 4 cases + the existing policy unit-test fixtures;
  - **safety invariant test**: for a grid of sanctions scores × doc states, any score ≥ threshold → `ESCALATE_COMPLIANCE` with `action is None`;
  - changing the threshold in an in-memory ontology copy changes the outcome (proves rules are data).
- [x] Encode the existing 4 branches as rules `R-AML-01`, `R-ID-01`, `R-EVID-01`, `R-CLEAR-01`.
- [x] Implement `app/ontology.py`: `load_ontology(path=None) -> Ontology`, `Ontology.evaluate(facts) -> PolicyVerdict`,
      `Ontology.tags_for(facts)`, `Ontology.required_policy(reason_key)`, `Ontology.version`.
- [x] Rewire `policy.evaluate/tags_for` to a module-level default ontology; keep `SANCTIONS_THRESHOLD`
      exported (read from the rule) for backward-compatible imports.
- [x] Replace hardcoded `REQUIRED_POLICY_BY_REASON` in `nodes.py` with `ontology.required_policy`.
- [x] Replace hardcoded `ONTOLOGY_PATH` in `agent.py` with the matched rule's path
      (e.g. `Evidence → R-EVID-01 → KYC-EVIDENCE-07 → Resolution`), and add `rule_id`/`rule_version`
      to the trace event of `policy_precheck` and to the action payload's `policy_versions`.
- [x] Full suite green. Commit: `feat: evaluate policy from an executable, versioned ontology`.

**Demo moment:** edit `R-AML-01` threshold 0.80 → 0.95 in a scratch copy, rerun KYC-1044 via eval
harness → outcome changes; revert. "Domain experts change rules; engineers don't ship code."

### Task 2: Captured expert judgment (the "3 a.m. call")

**Files:** `data/ontology.json`, `data/cases.json`, `app/ontology.py`, `app/tools.py`, `app/i18n.py`,
`app/domain.py`, `tests/test_ontology.py`, `tests/test_workflow_graph.py`

Encode a senior VN KYC reviewer heuristic for KYC-1043 (`Tran Minh Anh` vs `Tran My Anh`):

> Single-token name difference that is OCR-confusable or diacritic-only, liveness passed,
> tamper < 0.10 → **request a re-upload of the ID image** instead of full manual review.

- [x] Add a derived fact computed in `verify_documents` tool output (deterministic, unit-tested):
      `name_diff: {tokens_differing: int, ocr_confusable: bool, diacritic_only: bool}`
      (strip diacritics via `unicodedata`; small confusable table, e.g. `rn↔m`, `l↔1`, `0↔O`, prefix-truncation `My/Minh`).
- [x] Add rule `R-ID-EXP-01` (priority between `R-AML-01` and `R-ID-01`) → outcome `REQUEST_EVIDENCE`,
      action `request_document`, `documents: ["id_document_reupload"]`, reason_key `ocr_name_mismatch`,
      `source: {"kind": "expert", "ref": "Senior KYC Analyst interview, 2026-09", "captured_by": "FDE"}`.
- [x] Add a policy chunk `KYC-IDENTITY-11 §3.2 Low-quality capture` to `policies.json` so the rule is citable,
      plus EN/VI `REASON_TEMPLATES["ocr_name_mismatch"]`.
- [x] Add case `KYC-1046` — a **real** mismatch (`Nguyen Van Binh` vs `Pham Thi Lan`, 3 tokens) → must stay `MANUAL_REVIEW`.
      This proves the expert rule is bounded, not a loophole.
- [x] Resume path: after re-upload with `name_match: true`, cycle 2 reaches `CLEAR` (reuse existing document loop;
      `validate_submission` must accept `id_document_reupload`).
- [x] Update tests/evals that assert KYC-1043 → `MANUAL_REVIEW` (now `REQUEST_EVIDENCE` via expert rule); add KYC-1046 assertions.
- [x] `AgentDecision` gains optional `rule_source: dict | None` so UI can show "captured from expert".
- [x] Commit: `feat: capture an expert OCR-mismatch heuristic as a bounded cognitive rule`.

### Task 3: Dana vocabulary in trace and UI

**Files:** `app/workflow/nodes.py` (event metadata), `app/i18n.py`, `static/index.html`, `app/ui.py`

- [x] Add `phase` to every trace event: `SEE` (intake, 4 reads, evidence_gate), `THINK` (retrieve_policy,
      policy_precheck, openrouter_reason, reconcile_guard), `ACT` (action_review, execute_action,
      await_documents, validate_submission), `REFLECT` (finalize; Task 7 fills it).
- [x] Add `gov_stage` on the three governance events: `PROPOSE` (openrouter_reason), `VERIFY` (reconcile_guard),
      `COMMIT` (execute_action).
- [x] `AgentDecision` gains `governance: {reads: int, writes: int, proposals: int, overrides: int, approvals: int}`
      computed in `agent.py` from trace/tool_calls.
- [x] Static UI: group nodes into four phase columns; badges `Gov. read N / Gov. write N`; label the guard
      card "Propose → Verify → Commit". EN/VI strings.
- [x] Tests: surface tests assert `phase` present on all events and governance counters for KYC-1042 cycle 1 (reads=4, writes=0).
- [x] Commit: `feat: expose See/Think/Act/Reflect phases and governance counters`.

### Task 4: "Generic LLM + RAG" baseline for side-by-side

**Files:** Create `app/baseline.py`; modify `app/api.py`, `app/client.py`, `static/index.html`; create `tests/test_baseline.py`

- [ ] `BaselineRAGAgent.run(case_id, planner)`: retrieves policy chunks by keyword over **raw case JSON + case note**,
      asks the planner for an outcome, and **returns it directly** (no ontology, no guard, no approval). Read-only; never writes.
- [ ] `POST /api/compare {case_id, planner_mode, lang}` → `{baseline: {...outcome, rationale, would_execute}, governed: AgentDecision}`.
      The governed side is a normal run (its pending approval remains resumable).
- [ ] UI: "Compare with generic RAG" toggle → two columns; highlight disagreement in red with the violated rule id.
- [ ] Offline test with `CompromisedEvalPlanner`: baseline KYC-1044 → `CLEAR, would_execute=approve_account`; governed → `ESCALATE_COMPLIANCE`.
- [ ] Commit: `feat: side-by-side generic RAG baseline vs ontology-governed agent`.

### Task 5: P0 verification

- [ ] Full suite + `uv run python -m evals.run_evals` green.
- [ ] Manual run with `OPENROUTER_API_KEY`: KYC-1044 compare, KYC-1043 expert rule + re-upload → CLEAR, KYC-1046 manual, KYC-1042 flow.
- [ ] Screenshots into `.playwright-mcp/` for the deck. Commit.

---

## P1

### Task 6: Golden set and scorecard

**Files:** Create `evals/generate_cases.py`, `data/golden/cases.jsonl`, `evals/scorecard.py`, `tests/test_scorecard.py`

- [ ] Seeded generator (≥ 60 cases): mixes of sanctions scores near threshold (0.75–0.85), identity/OCR variants,
      missing evidence, clean, and 10 adversarial case notes (injection variants: "SYSTEM NOTE", VN-language injection,
      fake compliance approval, JSON-shaped instructions). Each row carries `expected_outcome` + `expected_rule_id`,
      labelled from the spec, **not** from the interpreter's output.
- [ ] `DomainTools` accepts an alternate cases source (constructor arg), default unchanged.
- [ ] `evals/scorecard.py --planner {eval,normal,compromised_demo,local} [--baseline]` prints and writes `evals/out/scorecard.json`:
      outcome agreement, rule-id agreement, straight-through rate (CLEAR/REQUEST_EVIDENCE without manual), override rate,
      **unsafe writes (must be 0)**, p50/p95 latency, tokens, cost/case, and estimated analyst minutes saved
      (configurable minutes-per-outcome table, documented as an assumption).
- [ ] Test: eval planner → 100% agreement, 0 unsafe; compromised → 0 unsafe, overrides > 0; baseline + compromised → unsafe > 0.
- [ ] Commit: `feat: labelled golden set and governance scorecard`.

### Task 7: Reflect loop (Dana Assurance-style, evidence-gated change)

**Files:** Create `app/reflect.py`, `data/amendments/` (gitignored runtime dir); modify `app/workflow/nodes.py` (finalize),
`app/api.py`, `static/index.html`; create `tests/test_reflect.py`

- [ ] On reviewer **rejection** (existing `review_result.reason`) or operational handoff, `finalize` emits a `REFLECT` trace event
      and records a `ReviewSignal {case_id, rule_id, rule_version, reviewer_reason, facts_snapshot}` (append-only, in `DomainTools`-style store).
- [ ] `propose_amendment(signal, planner)`: LLM drafts a **candidate rule** in the Task 1 schema (status `candidate`);
      the loader validates it (closed ops, no `case_note`) — invalid drafts are rejected, not repaired silently.
      Eval double provides a deterministic draft for tests.
- [ ] `replay(candidate, golden_set) -> ImpactReport {changed: [...], safety_regressions: int, agreement_before, agreement_after}`.
      Promotion is **blocked** if `safety_regressions > 0` or any hard-stop rule would be shadowed.
- [ ] `POST /api/amendments/{id}/promote` requires `{approver, approved: true}`; bumps ontology minor version, writes an audit record;
      rule never self-activates.
- [ ] UI "Reflect" tab: signal → candidate rule diff → impact report → Promote button (disabled on regressions).
- [ ] Tests: a candidate that lowers sanctions to "manual review" is blocked; a benign candidate is promotable and changes only its target cases.
- [ ] Commit: `feat: evidence-gated reflect loop from reviewer signals to ontology amendments`.

### Task 8: Authority matrix and quorum

**Files:** `data/ontology.json` (`authority` section), `app/workflow/nodes.py` (`action_review`), `app/domain.py`, tests

- [ ] `authority`: `request_document → 1 approver (analyst)`; `open_manual_review → maker-checker (2 distinct approvers)`;
      `promote_amendment → 2 of {kyc_lead, compliance}`. Sanctions stays "no automated action" (unchanged).
- [ ] `action_review` loops the interrupt until quorum is met; resume payload gains `approver` (required, distinct).
      Pending task payload shows `approvals: 1/2`.
- [ ] Idempotency key unchanged (payload-based); approvals recorded in trace with approver ids.
- [ ] Tests: same approver twice doesn't satisfy maker-checker; one rejection ends review.
- [ ] Commit: `feat: ontology-defined authority matrix with maker-checker quorum`.

### Task 9: Sovereign / small-model option

**Files:** `app/planner.py`, `.env.example`, `README.md`, `tests/test_workflow_planner.py`

- [ ] Add `planner_mode="local"`: `ChatOpenAI` against an OpenAI-compatible local endpoint (`LOCAL_LLM_BASE_URL`, default
      Ollama `http://127.0.0.1:11434/v1`, `LOCAL_LLM_MODEL` e.g. `qwen2.5:7b-instruct`). Same structured output + failure semantics.
- [ ] Mocked-transport test mirroring the OpenRouter adapter test; missing endpoint → configuration error before graph runs.
- [ ] Run scorecard for `normal` vs `local`, with and without `--baseline`; save the table for the deck
      ("7B + ontology ≈ large model; 7B alone unsafe"). Report honestly whatever the numbers are.
- [ ] Commit: `feat: local small-model planner for sovereign deployment`.

---

## P2

### Task 10: Runtime / pack split + second pack

**Files:** Create `packs/kyc/{ontology.json,policies.json,cases.json,pack.py}`, `packs/boiler/...`; modify `app/tools.py`,
`app/ontology.py`, `app/api.py` (`?pack=`), tests

- [ ] `Pack` protocol: `name, ontology_path, policies_path, cases_path, fact_tools: dict[str, Callable], action_allowlist, i18n_reasons`.
      `app/workflow` stays domain-agnostic; KYC specifics move to `packs/kyc`. Keep `data/` as a symlink or loader default for back-compat.
- [ ] `packs/boiler`: 3 cases (e.g. high flue-gas temp + low feedwater flow → `TRIP_ESCALATE`; tube-fouling signature →
      `SCHEDULE_CLEANING` needing approval; normal → `CLEAR`) on the **same graph**. Outcome enum becomes pack-provided.
- [ ] Test: both packs run through `build_workflow_graph` without runtime code changes (grep-based test: no `kyc` strings in `app/workflow`).
- [ ] Commit: `refactor: separate governed runtime from domain packs; add boiler pack`.

### Task 11: Curate — policy document → candidate rules

**Files:** Create `app/curate.py`, `data/sources/` (sample policy text, synthetic, clearly marked), `POST /api/curate`, tests

- [ ] Input: plain-text/markdown policy. LLM extracts candidate rules + citation spans; loader validates; each rule links
      to exact source span. Output goes through the **same** replay + promote gate as Task 7.
- [ ] Use a synthetic "internal AML circular" rather than quoting real regulation text.
- [ ] Commit: `feat: curate candidate ontology rules from policy documents`.

### Task 12: UI — live graph and lineage

**Files:** `static/index.html` (de-minify into readable sections), `app/api.py` (`GET /api/graph` → mermaid)

- [ ] Render the compiled LangGraph as mermaid (reuse `_print_graph_mermaid` logic, return string instead of print),
      highlight visited/current nodes.
- [ ] Lineage panel: fact → rule (id@version, source kind expert/policy) → citation → decision → approval → ticket.
- [ ] Event stream panel grouped by phase with governance counters (Task 3).
- [ ] Commit: `feat: live workflow graph and decision lineage view`.

---

## Task 13: Narrative, docs, deck

- [ ] README "Dana mapping" table:

| DanaOS concept | This repo |
|---|---|
| Structural ontology | `ontology.structural` |
| Cognitive ontology | `ontology.cognitive.rules` (policy + expert sources) |
| Dana Factory | `app/curate.py`, `app/reflect.py` candidate drafting |
| Dana Runtime | `app/workflow` + authority matrix |
| Dana Assurance | golden replay, impact report, promotion gate, scorecard |
| Propose → Verify → Commit | planner → ontology guard → quorum-approved idempotent gateway |
| See → Think → Act → Reflect | trace `phase` |
| Sovereign / small models | `planner_mode=local` |
| Vertical packs | `packs/kyc`, `packs/boiler` |

- [ ] Rewrite `docs/INTERVIEW_GUIDE.md` to the new 15-min flow:
  1. Problem in Dana terms (1m) · 2. KYC-1044 compare (3m) · 3. KYC-1043 expert rule + KYC-1046 boundary (3m) ·
  4. KYC-1042 Propose→Verify→Commit + quorum (3m) · 5. Reflect: rejection → candidate → replay → promote (3m) ·
  6. Scorecard + pack reuse (2m) + question: "Which customer workflow would you pilot first?"
- [ ] Update `slides/index.html` (Dana mapping slide, scorecard slide, before/after compare screenshot); keep `tests/test_concise_slides.py` green.
- [ ] Rehearse twice with a timer; record fallback path if OpenRouter is down (eval planner + recorded scorecard).
- [ ] Commit: `docs: align README, guide, and deck with the Dana-aligned demo`.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Rule interpreter regresses a safety branch | Task 1 invariant grid test; hard_stop rules can't be shadowed (Task 7 check) |
| Expert rule looks like a loophole | Bounded conditions + KYC-1046 negative case + citation to policy §3.2 |
| Claiming Dana features we don't have | Language: "a DanaOS-style pattern, built in a weekend", never "Dana" itself |
| Live LLM flakiness during interview | Eval planner fallback, pre-recorded scorecard JSON, screenshots |
| Scope creep | P0 alone is a complete, stronger demo; P1/P2 optional |

## Definition of done (P0)

- `data/ontology.json` is loaded and is the only source of decision logic; `policy.py` has no outcome branches.
- KYC-1043 resolves via a cited, expert-sourced rule; KYC-1046 stays manual.
- UI shows phases, governance counters, and a RAG-vs-governed compare.
- All tests + evals green; safety invariant test present.
