# Task 9 report — Local small-model integration (benchmark pending)

## What changed
- `app/planner.py`: `LocalPlanner` shares structured-output interface and failure semantics with
  OpenRouterPlanner, but points at `LOCAL_LLM_BASE_URL` (default Ollama `/v1`) and uses
  `LOCAL_LLM_MODEL` (default `qwen2.5:7b-instruct`); `local_endpoint_status` probes `/models`
  with a 2s timeout so unreachable endpoints fail before the graph starts.
- OpenRouterPlanner now validates outcome/action enum values from model output. Models sometimes
  return free text (`PENDING`, `Request proof of address document`) despite the tool schema;
  these must fail bounded retry and then fail closed, not be treated as valid recommendations.
- `local` added to agent, graph intake, API, CLI, static HTML, Streamlit, and baseline planner.
- `.env.example` documents local endpoint and model settings.
- `tests/test_local_planner.py`: mock OpenAI-compatible HTTP transport, covering local success,
  wrong proposal overridden, unreachable endpoint (API 400 before graph), defaults/env overrides.
  `tests/test_workflow_planner.py`: malformed enum regression.

## What was measured (honest limitations)
- No Ollama installation or model download was performed, so **no local/sovereign benchmark**.
- Hosted open-weight Qwen2.5-7B-Instruct and Qwen3-8B on OpenRouter, 10 sanctions cases each:
  0/10 valid proposals for both. The governed agent fell back to the hard-stop rule and stayed
  safe; this is **not** a successful small-model accuracy result. Full details:
  `docs/results/2026-09-27-small-model-probe.md`.
- Task 9's full local-vs-hosted scorecard remains open, pending a separately installed local
  inference server and a model with reliable structured output.

## Verification
- 228 tests OK, evals 24/24, golden set deterministic check passes.
