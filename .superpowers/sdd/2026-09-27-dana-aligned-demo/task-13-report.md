# Task 13 report — presentation wrap-up (human rehearsal pending)

## Delivered
- `slides/index.html`: 14 slides, as-built architecture/DanaOS-style mapping, executable rules,
  expert boundary case, risk-tiered authority, generic RAG comparison, labelled scorecard,
  Reflect replay/promotion, and explicit POC-vs-production limits.
- `slides/assets/`: committed WebP captures from the prior synthetic-data UI demos; deck is portable.
- `slides/index-concise.html`: retained its tested 9-slide technical structure, removed stale
  confidence claim and updated measured results, model options, authority and demo framing.
- `docs/INTERVIEW_GUIDE.md`: rewritten 15-minute flow, honest interpretations of the metrics,
  local-model limitation and provider-outage fallback.
- `docs/DEMO_RUNBOOK.md` and `README.md`: presentation commands and accurate as-built Dana mapping.
- `tests/test_main_deck.py`: slide count, evidence provenance, committed image existence, stale-count checks.
- Locally exported `slides/deck.pptx` (14 slides, speaker notes). It is gitignored; regenerate with
  `uv run --with python-pptx python slides/to_pptx.py`.

## Verified
- 231 unit tests OK; 24/24 deterministic eval checks.
- Headless Chrome render: all 14 slides load, images resolve, no JavaScript errors; each active
  slide's scrollHeight is 720 px at 1280×720 after fixing slides 2 and 13 overflow.
- PPTX export completed from the updated HTML and committed screenshots.

## Outstanding / not claimed
- A person still needs to rehearse twice with a timer. Provider-outage fallback is documented,
  not rehearsed by a human.
- Task 9's actual **local** model scorecard still requires a separately installed local server/model.
  Hosted 7–8B probes failed structured output and do not support small-model parity.
- P2 document curation, vertical packs, and live graph/lineage UI remain unimplemented.
