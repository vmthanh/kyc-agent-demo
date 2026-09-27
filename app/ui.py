"""Streamlit frontend for the KYC exception agent.

Talks to the agent runtime in `app.api` over HTTP rather than constructing an
agent in-process. Before this split each browser session built its own
`KYCExceptionAgent` with its own checkpointer, so two tabs could not see each
other's pending approvals.

Set `KYC_API_URL` if the runtime is not on http://127.0.0.1:8000.
"""
import os
import sys
from pathlib import Path

import streamlit as st

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import i18n
from app.client import KYCAPIError, KYCClient
from app.domain import PendingTaskKind

st.set_page_config(page_title="KYC Exception Agent / Tác Tử Xử Lý KYC", layout="wide")

if "client" not in st.session_state:
    st.session_state.client = KYCClient(os.environ.get("KYC_API_URL", "http://127.0.0.1:8000"))
client: KYCClient = st.session_state.client

lang = st.sidebar.selectbox("Language / Ngôn ngữ", ["en", "vi"], format_func=lambda l: {"en": "English", "vi": "Tiếng Việt"}[l])

st.title(i18n.ui_text(lang, "title"))
st.caption(i18n.ui_text(lang, "subtitle"))

planner_choice = st.sidebar.selectbox(
    i18n.ui_text(lang, "planner_label"),
    ["normal", "compromised_demo"],
    format_func=lambda mode: i18n.ui_text(lang, "planner_normal") if mode == "normal" else i18n.ui_text(lang, "planner_compromised"),
    help=i18n.ui_text(lang, "planner_help"),
)
try:
    cases = client.list_cases()
except KYCAPIError as exc:
    st.error(f"{i18n.ui_text(lang, 'title')}: agent runtime unreachable ({exc}).")
    st.stop()
case_id = st.selectbox(i18n.ui_text(lang, "case_label"), [c["case_id"] for c in cases], format_func=lambda cid: cid)

if st.button(i18n.ui_text(lang, "run"), type="primary"):
    try:
        st.session_state.decision = client.run(case_id, planner_mode=planner_choice, lang=lang)
    except KYCAPIError as exc:
        st.error(exc.message)

decision = st.session_state.get("decision")
if decision is not None and decision.lang != lang:
    # The language selector changed after a decision already existed --
    # re-render that same decision, don't silently leave stale English (or
    # Vietnamese) content on screen and don't re-run the graph (which would
    # lose an in-flight approval and duplicate tool calls).
    try:
        decision = client.relocalize(decision.decision_id, lang)
    except KYCAPIError as exc:
        st.error(exc.message)
        st.session_state.decision = None
        st.stop()
    st.session_state.decision = decision
if decision is not None:
    left, right = st.columns([2, 1])
    with left:
        st.subheader(decision.outcome_label)
        st.write(decision.summary)
        st.caption(f"{decision.model} · {decision.risk_label}")

        if decision.planner_mode == "compromised_demo":
            st.warning(i18n.ui_text(lang, "compromised_warning"))

        if decision.guardrail_override:
            st.error(f"{i18n.ui_text(lang, 'guardrail_override')}: {decision.guardrail_override}")
        else:
            st.success(i18n.ui_text(lang, "guardrail_ok"))

        st.markdown(f"**{i18n.ui_text(lang, 'planner_rationale')}**")
        st.write(decision.llm_rationale)
        confidence_note = (
            f"{i18n.ui_text(lang, 'confidence')}: {decision.proposal_confidence:.0%}"
            if decision.proposal_confidence is not None
            else f"{i18n.ui_text(lang, 'confidence')}: unavailable"
        )
        if decision.guardrail_override:
            confidence_note += f" ({i18n.ui_text(lang, 'overridden')})"
        if not decision.rationale_translated:
            confidence_note += f"  \n{i18n.ui_text(lang, 'translation_unavailable')}"
        st.caption(confidence_note)

        if decision.rule:
            rule = decision.rule
            kind = (rule.get("source") or {}).get("kind", "policy")
            st.info(
                f"**{i18n.ui_text(lang, 'rule_source')}: {rule['id']}@{rule['version']}** "
                f"({kind}) · {rule.get('cites') or ''}  \n{rule.get('description', '')}"
            )
        st.markdown(f"**{i18n.ui_text(lang, 'ontology_path')}**")
        st.write(" -> ".join(decision.ontology_path))

        st.markdown(f"**{i18n.ui_text(lang, 'grounded_facts')}**")
        for line in decision.facts:
            st.write(f"- {line}")

        st.markdown(f"**{i18n.ui_text(lang, 'policy_evidence')}**")
        for c in decision.citations:
            st.write(f"**{c.policy_id} v{c.version}** · {c.section}")
            st.caption(c.excerpt)

        if decision.pending_task and decision.pending_task.kind is PendingTaskKind.ACTION_APPROVAL:
            detail_bits = []
            if decision.approval.documents_label:
                detail_bits.append(f"{i18n.ui_text(lang, 'requested_documents')}: {decision.approval.documents_label}")
            st.warning(
                f"{i18n.ui_text(lang, 'approval_required')}: {decision.approval.action} "
                f"(key `{decision.approval.approval_key}`)" + ("\n\n" + "; ".join(detail_bits) if detail_bits else "")
            )
            st.caption(f"Pending case: {decision.case_id}")
            st.json(decision.approval.payload.get("action_payload", decision.approval.payload))
            authority = decision.pending_task.payload.get("authority") or {}
            so_far = authority.get("approvals_so_far", [])
            if authority:
                st.caption(f"Authority: {len(so_far)}/{authority.get('approvals', 1)} approvals · roles {', '.join(authority.get('roles', []))}"
                           + "".join(f"  \n✓ {a['approver']} ({a['role']})" for a in so_far))
            approver = st.text_input("Approver", value="lan.pham" if so_far else "an.nguyen", key=f"approver-{len(so_far)}")
            roles = authority.get("roles") or ["analyst"]
            role = st.selectbox("Role", roles, index=roles.index("kyc_lead") if so_far and "kyc_lead" in roles else 0, key=f"role-{len(so_far)}")
            if st.button(i18n.ui_text(lang, "approve_button")):
                try:
                    st.session_state.decision = client.resume(
                        decision.pending_task.interrupt_key, {"approved": True, "approver": approver, "role": role}, lang=lang
                    )
                    st.rerun()
                except KYCAPIError as exc:
                    st.error(exc.message)
            rejection_reason = st.text_input(i18n.ui_text(lang, "reject_reason"), key=f"reject-{decision.decision_id}")
            if st.button(i18n.ui_text(lang, "reject_button")):
                try:
                    st.session_state.decision = client.resume(
                        decision.pending_task.interrupt_key,
                        {"approved": False, "reason": rejection_reason},
                        lang=lang,
                    )
                    st.rerun()
                except KYCAPIError as exc:
                    st.error(exc.message)
        elif decision.pending_task and decision.pending_task.kind is PendingTaskKind.DOCUMENT_SUBMISSION:
            st.warning(i18n.ui_text(lang, "pending_document_submission"))
            st.json(decision.pending_task.payload.get("requested_documents", []))
            if st.button(i18n.ui_text(lang, "submit_documents")):
                try:
                    st.session_state.decision = client.resume(
                        decision.pending_task.interrupt_key,
                        {"documents": [
                            {"type": doc, "status": "verified"}
                            for doc in decision.pending_task.payload.get("requested_documents", [])
                        ]},
                        lang=lang,
                    )
                    st.rerun()
                except KYCAPIError as exc:
                    st.error(exc.message)
        elif decision.pending_task and decision.pending_task.kind is PendingTaskKind.OPERATIONAL_REVIEW:
            st.warning(i18n.ui_text(lang, "operational_handoff"))
            if st.button(i18n.ui_text(lang, "acknowledge_handoff")):
                try:
                    st.session_state.decision = client.resume(
                        decision.pending_task.interrupt_key, {"acknowledged": True}, lang=lang
                    )
                    st.rerun()
                except KYCAPIError as exc:
                    st.error(exc.message)
        if decision.executed_action:
            st.success(
                f"{i18n.ui_text(lang, 'action_executed')}: {decision.executed_action['action']} -> "
                f"{decision.executed_action['ticket_id']}"
                + (f" ({i18n.ui_text(lang, 'idempotent_replay')})" if decision.executed_action.get("replayed") else "")
            )
        if decision.review_result and decision.review_result["status"] == "rejected":
            st.warning(f"{i18n.ui_text(lang, 'action_rejected')}: {decision.review_result['reason']}")

    with right:
        st.subheader(i18n.ui_text(lang, "workflow_status"))
        st.caption(f"{decision.workflow_status.value} · {i18n.ui_text(lang, 'current_node')}: {decision.current_node}")
        st.caption(f"{i18n.ui_text(lang, 'cycle')}: {decision.cycle_count}/{decision.max_cycles}")
        st.caption(f"{i18n.ui_text(lang, 'planner_attempts')}: {decision.planner_attempts}")
        usage = decision.planner_usage or {}
        st.caption(f"{usage.get('total_tokens', 0)} {i18n.ui_text(lang, 'tokens')} · {i18n.ui_text(lang, 'cost')}: {usage.get('cost', i18n.ui_text(lang, 'not_available'))}")
        gov = decision.governance or {}
        st.caption(" · ".join(f"{k} {v}" for k, v in gov.items()))
        st.subheader(i18n.ui_text(lang, "agent_trace"))
        for event in decision.trace:
            icon = "\U0001f6a8" if event.status == "override" else "\u26a0\ufe0f" if event.status == "degraded" else "\u2022"
            stage = " · ".join(x for x in (event.phase, event.gov_stage) if x)
            st.markdown(f"{icon} `{stage}` **{event.title}**" if stage else f"{icon} **{event.title}**")
            st.caption(event.detail)

        st.subheader(i18n.ui_text(lang, "tool_inspection"))
        for call in decision.tool_calls:
            with st.expander(f"{call.name} · {call.duration_ms} ms"):
                st.json(call.output)
else:
    st.info(i18n.ui_text(lang, "select_prompt"))
