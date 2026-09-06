# Production Roadmap

## Phase 1: Discovery and thin slice (weeks 1-2)

- shadow five analysts and map the current exception workflow;
- define outcome taxonomy, ontology, and unsafe actions with Compliance;
- connect one read-only case API and one versioned policy source;
- agree on baseline metrics and a labeled evaluation set;
- deploy in recommendation-only mode.

Exit: domain owners sign off on ontology semantics and 50 golden cases pass safety checks.

## Phase 2: Controlled pilot (weeks 3-6)

- promote the OpenRouter planner from demo default to a production-SLA
  gateway: retries, circuit breakers, rate limits, prompt/model version pinning;
- add hybrid (embedding + tag) policy retrieval with jurisdiction/effective-date filters;
- persist workflow state and approval events;
- add OpenTelemetry traces, dashboards, cost/latency budgets, and replay;
- pilot with 5-10 analysts and compare against current handling.

Exit: no critical safety violations; target decision agreement >= 90%; p95 under 8 seconds; measurable handling-time reduction.

## Phase 3: Production hardening (weeks 7-10)

- tenant isolation, RBAC, secrets rotation, and audit retention;
- retries, circuit breakers, dead-letter queues, and idempotent writes;
- canary releases for prompts, policies, models, and tools;
- adversarial tests for injection, data exfiltration, and policy conflict;
- runbooks, ownership, SLOs, incident response, and rollback.

## Phase 4: Scale and reuse

- package ontology mappings and adapters as customer-specific configuration;
- build reusable exception-resolution and approval patterns;
- learn from overrides without automatically training on unreviewed outcomes;
- surface recurring platform gaps to product engineering.

## Scorecard

| Dimension | Metric |
|---|---|
| Task quality | outcome agreement; first-pass resolution |
| Grounding | citation precision/recall; unsupported-claim rate |
| Safety | policy-violation rate; unapproved side effects |
| Operations | handling time; override rate; escalation rate |
| Reliability | success rate; p95 latency; tool error rate |
| Economics | model/tool cost per resolved case |
