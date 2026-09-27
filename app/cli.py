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
        "--submit-requested-documents", "--submit-proof-of-address", dest="submit_documents", action="store_true",
        help="Submit the requested documents as verified after approving the evidence request.",
    )
    parser.add_argument(
        "--acknowledge-handoff", action="store_true",
        help="Acknowledge an operational handoff after the workflow pauses for review.",
    )
    args = parser.parse_args()

    agent = KYCExceptionAgent()
    decision = agent.run(args.case, planner_mode=args.planner, lang=args.lang)
    if args.approve and decision.pending_task and decision.pending_task.kind is PendingTaskKind.ACTION_APPROVAL:
        decision = agent.resume(decision.pending_task.interrupt_key, {"approved": True}, lang=args.lang)
        if args.submit_documents and decision.pending_task and decision.pending_task.kind is PendingTaskKind.DOCUMENT_SUBMISSION:
            requested = decision.pending_task.payload.get("requested_documents", [])
            decision = agent.resume(
                decision.pending_task.interrupt_key,
                {"documents": [{"type": doc, "status": "verified"} for doc in requested]},
                lang=args.lang,
            )
    elif args.reject and decision.pending_task and decision.pending_task.kind is PendingTaskKind.ACTION_APPROVAL:
        decision = agent.resume(
            decision.pending_task.interrupt_key,
            {"approved": False, "reason": args.reject},
            lang=args.lang,
        )
    if args.acknowledge_handoff and decision.pending_task and decision.pending_task.kind is PendingTaskKind.OPERATIONAL_REVIEW:
        decision = agent.resume(
            decision.pending_task.interrupt_key,
            {"acknowledged": True},
            lang=args.lang,
        )
    print(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
