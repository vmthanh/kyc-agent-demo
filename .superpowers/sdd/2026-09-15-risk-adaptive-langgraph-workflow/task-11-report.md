# Task 11 report

## Delivered

- Rewrote `README.md` for strict OpenRouter `normal` and `compromised_demo`
  modes, live smoke setup, safe failure behavior, eval doubles, and the
  current CLI/API commands.
- Replaced `docs/ARCHITECTURE.md` with the approved fan-out/fan-in Mermaid
  topology, named graph nodes, strict failure routes, pending-task protocol,
  cycle semantics, and payload-aware idempotency contract.
- Replaced `docs/INTERVIEW_GUIDE.md` with the approved 15-minute narrative,
  KYC-1042 two-cycle demo, KYC-1044 compromised live-mode contingency, and
  production discussion prompts.
- Revised full and concise HTML decks. The concise workflow slide now shows
  parallel grounding, evidence gate, policy precheck, OpenRouter, and the
  four explicit routes. Speaker notes distinguish live modes from eval
  doubles and explain independent agreement honestly.
- Updated the concise-deck tests to assert the new workflow labels and route
  diagram rather than a linear five-node layout.

## Verification

```text
.venv/bin/python -m unittest discover -s tests -v
Ran 112 tests in 9.861s
OK
```

The repository `uv run` command could not start in the sandbox because its
global uv cache is outside the permitted filesystem. The equivalent checked-in
`.venv` test runner passed. Headless Chrome was unavailable in this runtime;
the HTML deck was structurally checked by the concise-deck suite and all
changed slide markup was reviewed directly.
