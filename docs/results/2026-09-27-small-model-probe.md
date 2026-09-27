# Hosted small-model probe — 2026-09-27

**Not local inference.** These were hosted open-weight models on OpenRouter. No Ollama server/model was installed, so no sovereign/local benchmark was measured.

Commands (first 10 golden cases, all sanctions cases; 2 workers):

```bash
uv run python -m evals.scorecard --planner normal --model qwen/qwen-2.5-7b-instruct --baseline --limit 10 --workers 2
uv run python -m evals.scorecard --planner normal --model qwen/qwen3-8b --baseline --limit 10 --workers 2
```

| Model | Valid proposals | Governed final outcome | Baseline final outcome | Safety interpretation |
|---|---:|---|---|---|
| Qwen2.5-7B-Instruct | 0/10 | 10/10 ESCALATE_COMPLIANCE via fail-closed fallback | 0/10 labelled agreement; 10 planner failures | No unsafe writes, **not** successful model reasoning |
| Qwen3-8B | 0/10 | 10/10 ESCALATE_COMPLIANCE via fail-closed fallback | 0/10 labelled agreement; 10 planner failures | No unsafe writes, **not** successful model reasoning |

Both models returned free text in enum fields (e.g. outcome `PENDING`, action `Request proof of address document`). The planner now validates these fields before they reach the guard; invalid proposals trigger bounded retries and fail closed. The scorecard's 100% governed outcome accuracy on these 10 sanctions cases reflects the hard-stop rule/fallback, **not** competent 7–8B model proposals. No full 60-case run was completed for these models. Next experiment: a local model and serving stack that reliably supports tool-call/structured output; measure on the full golden set and report planner failures alongside agreement and cost.
