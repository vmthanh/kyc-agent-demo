# Task 8 report — Authority matrix and maker-checker

## What changed
- `data/ontology.json` → `authority`: roles (analyst, kyc_lead, compliance) and per-action requirements
  with `by_risk` tiers:
  - `request_document`: 1 approver; HIGH → 2 (maker-checker).
  - `open_manual_review`: 1; HIGH → 2; CRITICAL → 2 with distinct roles (kyc_lead + compliance).
  - `promote_amendment`: 2, distinct roles (one KYC Lead + one Compliance).
- `app/authority.py` (new): `requirement()`, `check_approval()` (role allowed, distinct approver, distinct role),
  `satisfied()`, `validate_matrix()` (every write action + promote covered; roles known; distinct-role counts feasible).
- `app/ontology.py`: loads and validates `authority` (`Ontology.authority`); errors surface as `OntologyError`.
- `app/workflow/nodes.py` `action_review`: loops `interrupt()` until the requirement is met or someone rejects;
  interrupt payload carries `authority` (+ `approvals_so_far`); trace `Approved 2/2: an.nguyen (analyst), lan.pham (kyc_lead)`.
  `execute_action` records `approved_by` on the result (outside the idempotency key).
- `app/agent.py`: validates an approval against the pending requirement *before* resuming (a refused approval
  leaves the task pending); `approve(key, approver=, role=)`; governance counts every approval.
- `app/reflect.py`: promotion uses the same matrix (`promote_amendment`) instead of a hardcoded role set.
- `app/api.py`: `/api/approve` accepts `approver`, `role`. `app/cli.py`: `--approvers name:role,...`.
- UIs: approval card shows `Authority n/m`, maker-checker badge, allowed roles, prior approvers, approver + role
  inputs with sensible next defaults (static + Streamlit); Reflect shows `Approvals n/2 (KYC Lead + Compliance)`.

## Verified LangGraph behavior
- Multiple `interrupt()` calls in one node resume in order under the same interrupt id (checked in a
  standalone script before building), so the pending key stays stable across approvals.

## Intentional behavior changes
- KYC-1046 (HIGH) now needs two distinct approvers; test/eval updated. Reflect promotion needs KYC Lead + Compliance.
- `review_result` gains `approvals` (+ `rejected_by`); `action_result` gains `approved_by`. Unit tests updated.

## Verification
- 222 tests OK (+8 `tests/test_authority.py`), evals 24/24.
- Live CLI KYC-1046 `--approve` → COMPLETED, approved_by [an.nguyen/analyst, lan.pham/kyc_lead].
- Headless Chrome: 0/2 → 1/2 → executed; Reflect 1/2 → PROMOTED with kyc_lead + compliance; no JS errors.
