"""Offline, deterministic scenario checks for the resumable KYC workflow."""
from __future__ import annotations

from app.agent import KYCExceptionAgent
from app.domain import Outcome, PendingTaskKind, WorkflowStatus
from app.tools import DomainTools, action_idempotency_key
from evals.planners import CompromisedEvalPlanner, PolicyMatchingEvalPlanner, UnavailableEvalPlanner


def main() -> None:
    checks: list[tuple[str, bool]] = []
    evidence_agent = KYCExceptionAgent()
    evidence = evidence_agent.run("KYC-1042", planner=PolicyMatchingEvalPlanner())
    checks += [
        ("KYC-1042: REQUEST_EVIDENCE", evidence.outcome == Outcome.REQUEST_EVIDENCE),
        ("KYC-1042: action approval pending", evidence.pending_task is not None and evidence.pending_task.kind is PendingTaskKind.ACTION_APPROVAL),
    ]
    evidence_approved = evidence_agent.approve(evidence.pending_task.interrupt_key)
    checks.append(("KYC-1042: document wait", evidence_approved.pending_task is not None and evidence_approved.pending_task.kind is PendingTaskKind.DOCUMENT_SUBMISSION))
    evidence_finished = evidence_agent.resume(evidence_approved.pending_task.interrupt_key, {"documents": [{"type": "proof_of_address", "status": "verified"}]})
    checks += [
        ("KYC-1042: CLEAR on cycle 2", evidence_finished.outcome == Outcome.CLEAR and evidence_finished.cycle_count == 2),
        ("KYC-1042: no pending task", evidence_finished.pending_task is None),
    ]

    manual_agent = KYCExceptionAgent()
    manual = manual_agent.run("KYC-1043", planner=PolicyMatchingEvalPlanner())
    checks.append(("KYC-1043: MANUAL_REVIEW", manual.outcome == Outcome.MANUAL_REVIEW))
    manual_finished = manual_agent.approve(manual.pending_task.interrupt_key)
    checks += [
        ("KYC-1043: action approval", manual_finished.executed_action is not None),
        ("KYC-1043: no pending task", manual_finished.pending_task is None),
    ]

    compromised = KYCExceptionAgent().run("KYC-1044", planner=CompromisedEvalPlanner())
    checks += [
        ("KYC-1044 compromised: ESCALATE_COMPLIANCE", compromised.outcome == Outcome.ESCALATE_COMPLIANCE),
        ("KYC-1044 compromised: override recorded", compromised.guardrail_override is not None),
        ("KYC-1044 compromised: no pending task", compromised.pending_task is None),
    ]
    unavailable_sanctions = KYCExceptionAgent().run("KYC-1044", planner=UnavailableEvalPlanner())
    checks += [
        ("KYC-1044 unavailable: ESCALATE_COMPLIANCE", unavailable_sanctions.outcome == Outcome.ESCALATE_COMPLIANCE),
        ("KYC-1044 unavailable: BLOCKED", unavailable_sanctions.workflow_status is WorkflowStatus.BLOCKED),
        ("KYC-1044 unavailable: no pending task", unavailable_sanctions.pending_task is None),
    ]

    clear = KYCExceptionAgent().run("KYC-1045", planner=PolicyMatchingEvalPlanner())
    checks += [
        ("KYC-1045: CLEAR", clear.outcome == Outcome.CLEAR),
        ("KYC-1045: no pending task", clear.pending_task is None),
    ]
    unavailable_clear = KYCExceptionAgent().run("KYC-1045", planner=UnavailableEvalPlanner())
    checks += [
        ("KYC-1045 unavailable: AWAITING_OPERATIONS", unavailable_clear.workflow_status is WorkflowStatus.AWAITING_OPERATIONS),
        ("KYC-1045 unavailable: AI_UNAVAILABLE", "reasoning is unavailable" in unavailable_clear.summary),
    ]

    tools = DomainTools()
    action = {"case_id": "KYC-1043", "action": "open_manual_review"}
    key = action_idempotency_key(action)
    first_ticket = tools.execute_approved_action(action, key)
    replay_ticket = tools.execute_approved_action(action, key)
    checks.append(("duplicate gateway request: one ticket ID", first_ticket["ticket_id"] == replay_ticket["ticket_id"] and replay_ticket["replayed"]))

    passed = sum(ok for _, ok in checks)
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(checks)} checks passed")
    raise SystemExit(0 if passed == len(checks) else 1)


if __name__ == "__main__":
    main()
