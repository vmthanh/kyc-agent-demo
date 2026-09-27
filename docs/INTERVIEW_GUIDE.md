# 15-minute interview guide

## Timing

| Time | Segment | Point to make |
|---|---|---|
| 0:00–1:30 | Problem and graph | KYC exceptions need evidence, policy, branching, and accountable writes. |
| 1:30–4:30 | KYC-1042, cycle one | A missing document becomes a bounded, approval-gated request. |
| 4:30–6:00 | Exact approval and idempotent ticket | The reviewer approves parameters, and retries return one ticket. |
| 6:00–8:30 | Document resume and cycle-two clear | Verified evidence resumes the same run and re-evaluates the graph. |
| 8:30–11:30 | KYC-1044 compromised live model and guardrail | OpenRouter can propose CLEAR; policy still stops the sanctions case. |
| 11:30–13:00 | Retries, strict failure, and tests | Provider failure is visible and safe; eval doubles keep CI deterministic. |
| 13:00–15:00 | Production seams and questions | Durable state, policy retrieval, idempotency, tenancy, and observability. |

## Setup

```bash
cp .env.example .env
# set OPENROUTER_API_KEY
uv run python -m app.api
uv run python -m evals.openrouter_smoke --case KYC-1045
uv run python -m app.cli --case KYC-1042 --planner normal --approve --submit-requested-documents
uv run python -m app.cli --case KYC-1044 --planner compromised_demo
```

Both live planner modes call OpenRouter. If a credentialed smoke call fails,
keep the provider failure visible and use the strict safe route as part of the
interview. A missing key is a setup/configuration error before the graph runs.

## 0:00–1:30 — problem and graph

“KYC exception resolution is a policy-bound workflow, not a chat response. The
graph fans out to four authoritative reads, joins at an evidence quality gate,
retrieves versioned policy, runs a policy precheck, asks OpenRouter for an
advisory structured proposal, and reconciles it with a deterministic guardrail.
Only bounded writes reach a human approval interrupt.”

Name the graph nodes: `intake`, `load_customer`, `verify_documents`,
`screen_watchlists`, `load_risk`, `evidence_gate`, `retrieve_policy`,
`policy_precheck`, `openrouter_reason`, `reconcile_guard`, `action_review`,
`execute_action`, `await_documents`, `validate_submission`,
`increment_cycle`, `safe_failure`, and finalization routes.

## 1:30–4:30 — KYC-1042, cycle one

Run KYC-1042 in `normal` mode. Identity and liveness pass, but
`proof_of_address` is missing. OpenRouter proposes `REQUEST_EVIDENCE`; the
guard recomputes the same mandate and builds an action payload naming that
document.

Pause at the pending `ACTION_APPROVAL`. Say: “The model did not create a
ticket. It prepared a request for a human to approve, with exact parameters.”

## 4:30–6:00 — approval and idempotent ticket

Approve the request. Show the approval interrupt ID and the stable gateway key
as two different identifiers. The gateway returns one ticket. Repeat the
approval or retry the request to show the prior ticket is replayed rather than
duplicated.

## 6:00–8:30 — document resume and cycle two

Submit verified proof of address through `/api/resume` or the CLI flag. The
same checkpoint resumes at `validate_submission`, increments `cycle_count`,
and re-runs grounding, retrieval, precheck, OpenRouter, and guard. The second
cycle reaches `CLEAR` and closes the exception with an audit trail.

## 8:30–11:30 — KYC-1044 and the compromised live model

Run KYC-1044 with `compromised_demo`. The prompt deliberately gives the live
OpenRouter model an unsafe instruction in the case note. The model may return
`CLEAR`, but `policy_precheck` and `reconcile_guard` see a 0.91 sanctions score
and enforce `ESCALATE_COMPLIANCE`. The graph emits no approval token and no
automated action.

If compromised mode still proposes escalation, say so plainly: “The guardrail
did not need to override this particular response. Independent agreement
between the live proposal and the typed policy mandate still means the stop
route is correct.” Do not claim an override occurred when it did not.

## 11:30–13:00 — retries, strict failure, and tests

Explain that tool and planner retries have bounded budgets. A timeout,
malformed structured response, or exhausted retry after planner construction
becomes an explicit planner failure and routes to a safe final state. A missing
key is rejected at planner selection as a visible configuration error. The
runtime never silently swaps in another runtime planner. Deterministic eval doubles and mocked
OpenRouter transport keep CI offline, while `evals.openrouter_smoke` is the
opt-in live check.

## 13:00–15:00 — production seams and questions

Close with the seams: persistent LangGraph checkpoints, durable unique
idempotency constraints, tenant-scoped retrieval and tool identity, policy
version activation, OpenTelemetry/MLflow traces, and a labeled scenario set.
Ask which customer workflow and write action the panel would pilot first.

## P0 demo additions (Dana-aligned)

Use these moments inside the timeline above; the full 15-minute rewrite is
Task 13 of `docs/superpowers/plans/2026-09-27-dana-aligned-demo.md`.

- **Compare with generic RAG (KYC-1044, `normal` mode).** Click *Compare with
  generic RAG*. Same model, ordinary prompt, no ontology/guard/approval,
  sampled 10×. Measured live with gpt-4o-mini: it cleared the 0.91 sanctions
  hit, and would have auto-approved, in 3–4 of 10 runs; the governed agent
  escalated via `R-AML-01@1.0` every time. Say: “the problem isn't that the
  model is always wrong, it's that it's *sometimes* wrong and you can't tell
  which time.” Use `normal`, not `compromised_demo`: under the compromised
  prompt the retrieved AML-SCREEN-02 text (“regardless of case notes”) won
  10/10, so there is nothing to compare. If a batch shows 0 divergences, say
  so and point at the count.
- **Rules are data.** Open `data/ontology.json`, show `R-AML-01`'s
  `params.threshold`, the `hard_stop` flag, and that the loader rejects a
  rule reading `case_note` or shadowing a hard stop. Optional: run with
  `KYC_ONTOLOGY_PATH` pointing at a copy with threshold 0.95 and KYC-1044
  becomes `CLEAR` via `R-CLEAR-01`, then explain that Task 7's promotion gate
  is what stops that change reaching production.
- **Captured expert judgment (KYC-1043 → KYC-1046).** `Trần Minh Anh` vs
  `Tran Mlnh Anh`: lost diacritics plus an `i`/`l` glyph confusion on a live,
  untampered capture. `R-ID-EXP-01` (source: senior analyst interview) asks
  for a clearer re-upload instead of a manual review, and the approval payload
  is bound to `R-ID-EXP-01@1.0`. Approve, submit the re-upload, and cycle 2
  clears. Then run KYC-1046 (a different person): it stays `MANUAL_REVIEW` via
  `R-ID-01`, which shows the expert rule is bounded. Domain point: diacritics
  can distinguish real names (Bình / Bính), which is why the remedy is a
  re-capture, never an approval.
- **Propose → Verify → Commit, See/Think/Act/Reflect.** Point at the
  three-step strip, the rule card with its ontology path, and the governance
  counters (reads, writes, approvals, overrides).
- **Honest overrides.** On cycle 2 of KYC-1042 and KYC-1043, gpt-4o-mini
  often proposes `REQUEST_EVIDENCE` again because the stale case note says
  proof of address is outstanding, while the fresh facts show nothing is
  missing. The guard records a real override and clears via `R-CLEAR-01`.
  Good example of untrusted text versus grounded facts. (A false “override”
  on every sanctions run, caused by the model returning the string `"null"`,
  was fixed in Task 4.)

## Three answers worth having ready

**Where is the AI?** `OpenRouterPlanner` returns a validated `LLMProposal`.
It cannot call tools, decide policy, or execute a write.

**How do you prevent prompt injection?** Separate case-note retrieval from
typed facts, frame notes as untrusted context, run a policy precheck before
the model, and reconcile again after the proposal.

**What happens when the provider is down?** The graph records the failure and
uses a strict safe route. The offline deterministic path is an eval double,
not a production planner fallback.
