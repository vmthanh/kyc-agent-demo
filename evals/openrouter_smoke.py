"""Credential-gated, one-call OpenRouter preflight for a grounded case."""
from __future__ import annotations

import argparse
import os
import sys
from time import perf_counter

from app.planner import OpenRouterPlanner
from app.policy import tags_for
from app.tools import DomainTools
from app.domain import Outcome


def _ground_case(case_id: str) -> tuple[dict, list, str]:
    tools = DomainTools()
    facts = {
        name: tools.call(name, "OpenRouter smoke preflight grounding", {"case_id": case_id}).output
        for name in ("get_case", "verify_documents", "screen_sanctions", "get_risk_profile")
    }
    return facts, tools.retrieve_policy(tags_for(facts)), tools.get_case_note(case_id)


def _validate_proposal(proposal) -> None:
    if proposal.outcome not in {outcome.value for outcome in Outcome}:
        raise ValueError("provider returned an invalid outcome")
    if proposal.action not in {None, "request_document", "open_manual_review"}:
        raise ValueError("provider returned an invalid action")
    if not isinstance(proposal.rationale, str) or not proposal.rationale.strip():
        raise ValueError("provider returned an empty rationale")
    if not isinstance(proposal.confidence, (int, float)) or not 0 <= proposal.confidence <= 1:
        raise ValueError("provider returned invalid confidence")
    if not isinstance(proposal.model, str) or not proposal.model.strip():
        raise ValueError("provider returned no model")
    if not isinstance(proposal.usage, dict):
        raise ValueError("provider returned invalid usage metadata")
    for key in ("input_tokens", "output_tokens", "total_tokens", "cost"):
        if key in proposal.usage and not isinstance(proposal.usage[key], (int, float)):
            raise ValueError(f"provider returned invalid {key}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one opt-in grounded OpenRouter planner call")
    parser.add_argument("--case", default="KYC-1045")
    parser.add_argument("--mode", choices=("normal", "compromised_demo"), default="normal")
    args = parser.parse_args(argv)

    if not os.getenv("OPENROUTER_API_KEY", "").strip() or os.getenv("OPENROUTER_API_KEY") == "your_key_here":
        print("OPENROUTER_API_KEY is required for the live smoke preflight", file=sys.stderr)
        return 2

    try:
        facts, citations, case_note = _ground_case(args.case)
        planner = OpenRouterPlanner(compromised=args.mode == "compromised_demo")
        started = perf_counter()
        proposal = planner.propose(args.case, facts, citations, case_note)
        latency_ms = int((perf_counter() - started) * 1000)
        _validate_proposal(proposal)
    except Exception as exc:
        print(f"OpenRouter smoke preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    usage = proposal.usage
    token_text = "/".join(str(usage.get(key, "n/a")) for key in ("input_tokens", "output_tokens", "total_tokens"))
    print(f"model={proposal.model}")
    print(f"latency_ms={latency_ms}")
    print(f"tokens={token_text}")
    print(f"provider_cost={usage.get('cost', 'n/a')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
