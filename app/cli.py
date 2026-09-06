import argparse
import json

from .agent import KYCExceptionAgent


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the KYC exception agent on a synthetic case.")
    parser.add_argument("--case", default="KYC-1042")
    parser.add_argument(
        "--planner",
        choices=["auto", "openrouter", "heuristic", "adversarial"],
        default="auto",
        help="auto uses OpenRouter if OPENROUTER_API_KEY is set, else the offline heuristic fallback. "
        "adversarial simulates a compromised model to demonstrate the guardrail override.",
    )
    parser.add_argument("--lang", choices=["en", "vi"], default="en", help="Display language for the decision.")
    parser.add_argument("--approve", action="store_true", help="Auto-approve the proposed action, if any.")
    args = parser.parse_args()

    agent = KYCExceptionAgent()
    decision = agent.run(args.case, planner_name=args.planner, lang=args.lang)
    if args.approve and decision.approval:
        decision = agent.approve(decision.approval.approval_key)
    print(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
