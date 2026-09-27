# Progress — Dana-Aligned Demo Upgrade

Plan: `docs/superpowers/plans/2026-09-27-dana-aligned-demo.md`
Baseline (2026-09-27): `uv run python -m unittest discover -s tests` → 139 tests, OK.

| Task | Status | Commit | Tests after | Notes |
|---|---|---|---|---|
| 0 Housekeeping | ✅ done | a04a09f | 139 OK | `app.server` → `app.api` in interview guide; README Dana mapping stub |
| 1 Executable ontology | ✅ done | b5b491f | 163 OK, evals 19/19 | rules are data; see `task-1-report.md` |
| 2 Expert rule | ✅ done | 5204b7c | 179 OK, evals 22/22 | KYC-1043 via R-ID-EXP-01; KYC-1046 boundary; see `task-2-report.md` |
| 3 Dana vocabulary | ✅ done | f1e6dd5 | 185 OK, evals 22/22 | phases, P→V→C, governance counters, rule card; see `task-3-report.md` |
| 4 RAG baseline compare | ✅ done | 58c51dd | 197 OK, evals 22/22 | baseline unsafe 3/10 on KYC-1044; fixed false-override bug; see `task-4-report.md` |
| 5 P0 verification | ✅ done | eba5063 | 199 OK, evals 22/22 | live walkthrough all paths; fixed MLflow startup hang; see `task-5-report.md` |
| **P0 phase** | ✅ **complete** | | | |
| 6 Golden set + scorecard | ✅ done | see git log | 205 OK, evals 22/22 | live: governed 100%/0 unsafe vs baseline 71.7%/2 unsafe; see `task-6-report.md` |
| 7 Reflect loop | ⏳ in progress | | | |
| 8 Authority matrix | ⬜ todo | | | |
| 9 Local small model | ⬜ todo | | | |
| 10–13 (P2, wrap) | ⬜ todo | | | |
