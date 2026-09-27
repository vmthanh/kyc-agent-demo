# Task 2 report — Captured expert judgment

## What changed
- `app/names.py` (new): `fold_diacritics`, `ocr_canonical`, `name_diff(declared, document)` →
  `{tokens_differing, diacritic_only, ocr_explainable, declared_token, document_token}`.
- `app/tools.py`: `verify_documents` output now includes `name_diff` (a grounded fact). A verified
  `id_document_reupload` is the synthetic stand-in for re-OCR of a clearer capture → `name_match: true`.
- `app/ontology.py`: leaf option `"missing": "false"` (leaf doesn't hold if the fact is absent);
  rejected under `not`; unknown leaf keys rejected. Core facts still fail closed. New `rule_view()`.
- `data/ontology.json`: `R-ID-EXP-01` (priority 20, `source.kind = "expert"`, cites `KYC-IDENTITY-12`),
  bounded by: name mismatch, liveness passed, no missing fields, tamper < 0.10, `ocr_explainable`.
- `data/policies.json`: `KYC-IDENTITY-12 §3.2 Low-quality identity capture`.
- `data/cases.json`: KYC-1043 now `Trần Minh Anh` vs `Tran Mlnh Anh`; new KYC-1046 (`Nguyễn Văn Bình` vs `Phạm Thị Lan`).
- `app/domain.py`: `AgentDecision.rule` = `{id, version, cites, source, description}`.
- `app/i18n.py`: `ocr_name_mismatch` reason (EN/VI), `id_document_reupload` label, name-mismatch fact line, VI excerpt.
- `app/cli.py`: `--submit-requested-documents` (old `--submit-proof-of-address` kept as alias; both submit what was requested).
- `app/ui.py` (Streamlit): submits the requested documents instead of hardcoded proof of address.

## Deviations from plan (deliberate)
- New policy id `KYC-IDENTITY-12` instead of a second section of `KYC-IDENTITY-11`: `policy_precheck`
  treats two different excerpts under one policy id as contradictory and fails closed.
- KYC-1043's document name changed from `Tran My Anh` to `Tran Mlnh Anh`: "Minh"→"My" is not an OCR
  error, so the honest heuristic would (correctly) not fire. The new pair combines lost diacritics
  with an `i`/`l` glyph confusion, which is realistic for poor VN ID captures.
- `rule_source` became `rule` (id + version + cites + source + description) for the UI in Task 3.

## Intentional behavior changes (tests updated)
- KYC-1043: `MANUAL_REVIEW` → `REQUEST_EVIDENCE` via `R-ID-EXP-01`; clears on cycle 2 after re-upload.
- Manual-review tests/evals moved to KYC-1046.

## Verification
- Unit tests: 179 OK (163 + 13 in `tests/test_expert_rule.py` + 3 new trajectory/agent tests).
- Evals: 22/22.
- Live (`normal`, gpt-4o-mini): KYC-1043 → REQUEST_EVIDENCE, no override; model rationale cites KYC-IDENTITY-12.
