import argparse
import json

from .agent import KYCExceptionAgent
from .domain import PendingTaskKind


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the KYC exception agent on a synthetic case.")
    parser.add_argument("--case", default="KYC-1042")
    parser.add_argument(
        "--planner",
        choices=["normal", "compromised_demo"],
        default="normal",
        help="Select the normal live planner or the compromised demo mode.",
    )
    parser.add_argument("--lang", choices=["en", "vi"], default="en", help="Display language for the decision.")
    resolution = parser.add_mutually_exclusive_group()
    resolution.add_argument("--approve", action="store_true", help="Auto-approve the proposed action, if any.")
    resolution.add_argument("--reject", metavar="REASON", help="Reject the proposed action with an audit reason.")
    parser.add_argument(
        "--submit-proof-of-address", action="store_true",
        help="Submit verified proof of address after approving the evidence request.",
    )
    args = parser.parse_args()

    agent = KYCExceptionAgent()
    decision = agent.run(args.case, planner_mode=args.planner, lang=args.lang)
    if args.approve and decision.pending_task and decision.pending_task.kind is PendingTaskKind.ACTION_APPROVAL:
        decision = agent.resume(decision.pending_task.interrupt_key, {"approved": True}, lang=args.lang)
        if args.submit_proof_of_address and decision.pending_task and decision.pending_task.kind is PendingTaskKind.DOCUMENT_SUBMISSION:
            decision = agent.resume(
                decision.pending_task.interrupt_key,
                {"documents": [{"type": "proof_of_address", "status": "verified"}]},
                lang=args.lang,
            )
    elif args.reject and decision.pending_task and decision.pending_task.kind is PendingTaskKind.ACTION_APPROVAL:
        decision = agent.resume(
            decision.pending_task.interrupt_key,
            {"approved": False, "reason": args.reject},
            lang=args.lang,
        )
    print(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
