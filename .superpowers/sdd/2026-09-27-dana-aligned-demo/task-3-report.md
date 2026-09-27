# Task 3 report — Dana vocabulary in trace and UI

## What changed
- `app/workflow/nodes.py`: `PHASE_BY_STEP` (SEE/THINK/ACT/REFLECT) and `GOV_STAGE_BY_STEP`
  (openrouter_reason=PROPOSE, reconcile_guard=VERIFY, action_review/execute_action=COMMIT).
  Every trace event carries `phase` and `gov_stage`; `action_review` events record `metadata.approved`.
- `app/domain.py`: `TraceEvent.phase`, `TraceEvent.gov_stage` (defaults `None`, so older checkpoints load);
  `AgentDecision.governance` = reads, proposals, verifications, overrides, approvals, rejections, writes.
- `app/agent.py`: `_governance()` derives counters from trace + tool calls across all cycles.
- `static/index.html`: workflow graph grouped into See/Think/Act/Reflect columns (obsolete
  `safe_failure`/`finalize_blocked`/`finalize_rejected` nodes removed); "Governed write:
  Propose → Verify → Commit" strip; rule card (id@version, expert/policy badge, cited policy,
  description, source, ontology path); governance badges; trace items tagged by phase/stage. EN + VI.
- `app/ui.py` (Streamlit): rule attribution box, governance caption, phase/stage tags in trace.

## Deviation
- `action_review` is tagged COMMIT together with `execute_action` (the human authority gate is part of
  the commit), rather than introducing a fourth stage.

## Verification
- 185 tests OK (+6 in `tests/test_governance_surface.py`), evals 22/22.
- Headless Chrome against the live API (KYC-1043, OpenRouter normal): page renders with no JS errors;
  screenshot reviewed (phase columns, P→V→C strip, expert rule card, governance badges).

## Found along the way (fix in Task 5)
- With `.env`'s `MLFLOW_TRACKING_URI=http://127.0.0.1:5001` and no MLflow server running,
  `app.api` startup hangs in `mlflow.set_experiment` retries. Live-demo hazard.
