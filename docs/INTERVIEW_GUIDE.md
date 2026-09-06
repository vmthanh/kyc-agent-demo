# Round 1 Demo Guide

## 45-minute flow

| Segment | Time | Message |
|---|---:|---|
| Problem definition | 5 min | KYC exception handling is a multi-system, policy-bound workflow. |
| Why agentic | 5 min | It needs state, tool calls, branching, and a model that proposes -- not a chat response. |
| Architecture | 10 min | LangGraph turns the workflow into a graph; a deterministic guardrail is the actual safety boundary. |
| Live demo | 12 min | Happy path + approval, the clean/no-action case, the sanctions stop, then the adversarial override. |
| Engineering discussion | 8 min | Evaluation, debugging, prompt injection, and production rollout. |
| Questions | 5 min | Discuss customer deployment and reusable patterns. |

## Opening (30 seconds)

"I chose a KYC exception-resolution workflow because it connects my hands-on
KYC experience at Shopee with the banking AI platforms I lead at
Techcombank. The technical point isn't the UI -- it's that a planner
proposes a resolution, and a deterministic guardrail I can prove holds even
when the model doesn't."

## Why this needs an agent, not only RAG

RAG retrieves policy text; it doesn't manage the multi-step state machine,
select and validate tool calls, pause for a reviewer, recover after
interruption, or produce an auditable trajectory. LangGraph owns the
workflow. The planner (OpenRouter, or an offline heuristic fallback when no
key is configured) proposes an outcome; `app/policy.py` independently
recomputes the mandated outcome from the same typed facts and overrides the
planner when they disagree.

## Problem framing (45 seconds)

KYC analysts reconcile customer data, document verification, sanctions
screening, risk scores, and versioned policy. A chatbot can summarize these
inputs but can also invent policy or act on a manipulated instruction. This
agent grounds a typed case graph and lets a deterministic policy engine, not
the model, decide what's permitted.

Ask the panel to watch for four things:

1. domain grounding through typed tool calls, not free text;
2. a planner proposal that is explicitly advisory;
3. a guardrail that can and does override the model;
4. explicit human approval before any side effect.

## Live demo script (4 cases, ~6 minutes)

Run `uv run python -m app.server` and open `http://localhost:8000` (or use
the CLI: `uv run python -m app.cli --case <id> --planner <mode>`).

### KYC-1042: missing proof of address -> REQUEST_EVIDENCE

- Planner: `auto` or `heuristic`. Run it.
- Point to the planner rationale panel -- explicitly advisory.
- Point to the "guardrail check" banner -- confirms agreement, not a rubber
  stamp; a real check ran.
- Click **Approve bounded action**. Point out the idempotency key and the
  created ops ticket.

### KYC-1045: clean case -> CLEAR

- Run it. No approval button appears because there is nothing to approve --
  not because the agent is stuck. This is the "knows when to do nothing"
  case that most agent demos skip.

### KYC-1044: possible sanctions match -> ESCALATE_COMPLIANCE

- Run it with the default planner. No approval token: automated action is
  prohibited outright, not merely pending review.

### KYC-1044 again, planner = `adversarial` -- the centerpiece

- This case's `case_note` contains a live prompt-injection attempt asking
  the model to ignore the screening score and respond CLEAR. Switch the
  planner to `adversarial` (a simulated compromised model that obeys it) and
  run the same case.
- The planner panel shows it recommending CLEAR. The result is still
  `ESCALATE_COMPLIANCE`, and a red "Guardrail override" banner explains
  exactly why.

Say: "This isn't a slide claim -- `evals/run_evals.py` runs this exact
scenario on every change and fails the build if the guardrail ever agrees
with the compromised planner."

### Bonus: flip the language selector to Tiếng Việt

- Re-run any case with `lang=vi`. The outcome, summary, guardrail override,
  and heuristic/adversarial rationale all re-render in Vietnamese with no
  network call -- they come from hand-written templates in `app/i18n.py`
  keyed by stable English IDs (`reason_key`, `rationale_key`), not from
  post-hoc string translation of already-rendered English sentences.
- Point out `proposal_confidence` next to the rationale, not next to the
  outcome badge, and that it is explicitly labeled "(overridden)" when the
  guardrail disagreed -- a 97%-confident wrong answer must never look like
  97% confidence in the right one.

Say: "For a Vietnam-based deployment this is the actual localization
question a customer will ask on day one: can the compliance-critical text
be trusted in the local language, or is it a bolted-on translation layer?
Here the answer is templated where the content is deterministic, and it
degrades honestly -- falls back to English and says so -- where it isn't."

## Architecture explanation (90 seconds)

The graph is: **ground** (allowlisted read tools build typed facts) ->
**retrieve** (versioned policy citations from the same facts) -> **reason**
(the planner proposes an outcome) -> **guard** (deterministic policy
recomputes the mandate and overrides on disagreement) -> **review** (a write
action pauses on a LangGraph `interrupt()` for human approval; a no-action
outcome skips straight through) -> **execute** (idempotency-keyed action
gateway, only after approval).

`app/planner.py` is the only extension seam for a new model. It must return
an `LLMProposal` -- nothing more. It cannot reach the policy engine, the
action gateway, or bypass approval.

## Debugging story (90 seconds)

"An early version let the planner see the full case record, including free
text, with no separation between data and instruction. During adversarial
testing I wrote a case note that told the model the sanctions hit was a
false positive and to clear it -- and a naive prompt would have complied. I
moved the sanctions threshold and every other mandated rule into
`policy.py`, a pure function with no access to free text, and had the graph
recompute it independently of whatever the planner said. Now I keep an
`AdversarialPlanner` that simulates a compromised model and run it in CI
(`evals/run_evals.py`) so a regression here fails the build, not a demo."

Debug in this order:

- replay the trace with fixed data and a pinned planner/model version
  (every `AgentDecision` records `model`, the planner's rationale, and any
  `guardrail_override`);
- localize the fault: ground/retrieve (data), reason (planning), guard
  (policy), or execute (action) -- the trace names the step;
- compare typed intermediate state (facts, citations, proposal), not only
  the final summary;
- add the failing case to `data/cases.json` and a golden-outcome assertion
  in `evals/run_evals.py`;
- change one component (prompt, model, policy rule) and re-run the full
  eval suite before shipping.

## Likely technical questions

**Where is the AI in this demo?**
`app/planner.py`. With `OPENROUTER_API_KEY` set, `OpenRouterPlanner` makes a
real structured-output call. Without one, `HeuristicPlanner` is a
transparent, labeled offline fallback so the demo doesn't depend on network
access or a paid key live. Either way, the planner's output is advisory --
`app/policy.py` makes the actual decision.

**Why an ontology rather than a vector store alone?**
Embeddings help recall similar text; the ontology captures entities,
relations, constraints, and valid actions. Use both: the ontology structures
the case and drives tag-based policy retrieval; a production system would
add hybrid (embedding + tag) retrieval over a larger policy corpus.

**How do you prevent prompt injection?**
Structurally: case notes are fetched through a dedicated `get_case_note`
tool, never merged into the facts `policy.py` reads, and the planner's
system prompt frames them as data, not instruction. Then defense in depth:
even if the model is fully compromised, the deterministic guardrail
recomputes the mandate from typed facts alone and overrides it. `KYC-1044`'s
case note carries a real injection attempt and `evals/run_evals.py` proves
the override, on every run.

**How do you evaluate it?**
`evals/run_evals.py` checks outcome correctness, citation presence, trace
completeness, safety (no unapproved side effects), and one adversarial
guardrail-override case. In production, add a labeled scenario set, sampled
human review of guardrail overrides, and dashboards on override rate,
decision agreement, and cost/latency per case.

**How do you handle policy changes?**
Version policy chunks (`policy_id` + `version`, already in every citation
and decision), include effective dates and jurisdiction, invalidate
retrieval indexes atomically, and replay the golden case suite before
activating a new version.

**How do you handle multiple customers?**
Keep `agent.py`'s graph stable; swap `data/ontology.json`, `policies.json`,
and `DomainTools` per customer as configuration, not code forks. Locale is
the same pattern: `app/i18n.py`'s templates and `POLICY_EXCERPTS_VI` would
become a versioned, per-locale artifact owned by Compliance in each market,
not a hardcoded dict. Promote a pattern to the platform only after it's
validated with more than one customer's domain owners.

**How do you localize a compliance-critical agent?**
Split by whether the content is deterministic or free-form. Deterministic
content (policy verdicts, guardrail overrides, citations, UI chrome) is
templated per language and rendered offline -- accurate, fast, and
auditable, because Compliance can review the exact Vietnamese sentence a
customer will see, the same way they review the English one.  Free-form LLM
output gets a best-effort machine translation with an explicit fallback
(`i18n.translate_via_llm` returns `None` on any failure, and the UI shows
the English original with a note rather than a silently wrong translation).
I would never machine-translate the safety-critical sentences themselves in
production; the templates keep that off the table entirely.

## Close (20 seconds)

"This demo reflects how I work as an FDE: start from the customer workflow,
make the domain explicit, build the smallest end-to-end slice, prove the
safety property with a running eval instead of a slide, and leave a clear
seam for hardening into production."
