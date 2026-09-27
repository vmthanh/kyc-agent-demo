# Progress — Dana-Aligned Demo Upgrade

Plan: `docs/superpowers/plans/2026-09-27-dana-aligned-demo.md`
Baseline (2026-09-27): `uv run python -m unittest discover -s tests` → 139 tests, OK.

| Task | Status | Commit | Tests after | Notes |
|---|---|---|---|---|
| 0 Housekeeping | ✅ done | a04a09f | 139 OK | `app.server` → `app.api` in interview guide; README Dana mapping stub |
| 1 Executable ontology | ✅ done | b5b491f | 163 OK, evals 19/19 | rules are data; see `task-1-report.md` |
| 2 Expert rule | ✅ done | 5204b7c | 179 OK, evals 22/22 | KYC-1043 via R-ID-EXP-01; KYC-1046 boundary; see `task-2-report.md` |
| 3 Dana vocabulary | ✅ done | see git log | 185 OK, evals 22/22 | phases, P→V→C, governance counters, rule card; see `task-3-report.md` |
| 4 RAG baseline compare | ⬜ todo | | | |
| 5 P0 verification | ⬜ todo | | | |
| 6–13 | ⬜ todo | | | |
