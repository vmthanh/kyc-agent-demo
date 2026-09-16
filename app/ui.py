"""Streamlit surface: shows LangGraph's native interrupt/resume approval flow
and (optionally) MLflow tracing, backed by the same `KYCExceptionAgent` used
by the CLI and the zero-dependency HTTP demo in `app/server.py`.

One agent is kept for the whole session (not one per planner choice): the
planner and the display language are arguments to `run()`/`approve()`, so
switching either between a run and its approval can never target a stale or
wrong graph (see `agent.py`'s docstring)."""
import os
import sys
from pathlib import Path

import streamlit as st

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import i18n
from app.agent import KYCExceptionAgent
from app.domain import PendingTaskKind
from app.tools import DomainTools

st.set_page_config(page_title="KYC Exception Agent / Tác Tử Xử Lý KYC", layout="wide")

MLFLOW_ENABLED = False
try:
    import mlflow

    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5001"))
    mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT_NAME", "kyc-exception-agent-demo"))
    mlflow.langchain.autolog()
    MLFLOW_ENABLED = True
except Exception as exc:  # pragma: no cover - best-effort tracing only
    MLFLOW_STATUS = f"MLflow tracing disabled: {exc}"
else:
    MLFLOW_STATUS = f"MLflow tracing active -> {os.getenv('MLFLOW_TRACKING_URI', 'http://127.0.0.1:5001')}"

if "tools" not in st.session_state:
    st.session_state.tools = DomainTools()
if "agent" not in st.session_state:
    st.session_state.agent = KYCExceptionAgent(tools=st.session_state.tools)
agent: KYCExceptionAgent = st.session_state.agent

lang = st.sidebar.selectbox("Language / Ngôn ngữ", ["en", "vi"], format_func=lambda l: {"en": "English", "vi": "Tiếng Việt"}[l])

st.title(i18n.ui_text(lang, "title"))
st.caption(i18n.ui_text(lang, "subtitle"))
st.sidebar.info(MLFLOW_STATUS if MLFLOW_ENABLED else f":warning: {MLFLOW_STATUS}")

planner_choice = st.sidebar.selectbox(
    i18n.ui_text(lang, "planner_label"),
    ["normal", "compromised_demo"],
    format_func=lambda mode: i18n.ui_text(lang, "planner_normal") if mode == "normal" else i18n.ui_text(lang, "planner_compromised"),
    help=i18n.ui_text(lang, "planner_help"),
)
cases = st.session_state.tools.list_cases()
case_id = st.selectbox(i18n.ui_text(lang, "case_label"), [c["case_id"] for c in cases], format_func=lambda cid: cid)

if st.button(i18n.ui_text(lang, "run"), type="primary"):
    st.session_state.decision = agent.run(case_id, planner_mode=planner_choice, lang=lang)

decision = st.session_state.get("decision")
if decision is not None and decision.lang != lang:
    # The language selector changed after a decision already existed --
    # re-render that same decision, don't silently leave stale English (or
    # Vietnamese) content on screen and don't re-run the graph (which would
    # lose an in-flight approval and duplicate tool calls).
    decision = agent.relocalize(decision.decision_id, lang)
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
            if st.button(i18n.ui_text(lang, "approve_button")):
                st.session_state.decision = agent.resume(
                    decision.pending_task.interrupt_key, {"approved": True}, lang=lang
                )
                st.rerun()
            rejection_reason = st.text_input(i18n.ui_text(lang, "reject_reason"), key=f"reject-{decision.decision_id}")
            if st.button(i18n.ui_text(lang, "reject_button")):
                try:
                    st.session_state.decision = agent.resume(
                        decision.pending_task.interrupt_key,
                        {"approved": False, "reason": rejection_reason},
                        lang=lang,
                    )
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        elif decision.pending_task and decision.pending_task.kind is PendingTaskKind.DOCUMENT_SUBMISSION:
            st.warning(i18n.ui_text(lang, "pending_document_submission"))
            st.json(decision.pending_task.payload.get("requested_documents", []))
            if st.button(i18n.ui_text(lang, "submit_documents")):
                st.session_state.decision = agent.resume(
                    decision.pending_task.interrupt_key,
                    {"documents": [{"type": "proof_of_address", "status": "verified"}]},
                    lang=lang,
                )
                st.rerun()
        elif decision.pending_task and decision.pending_task.kind is PendingTaskKind.OPERATIONAL_REVIEW:
            st.warning(i18n.ui_text(lang, "operational_handoff"))
            if st.button(i18n.ui_text(lang, "acknowledge_handoff")):
                st.session_state.decision = agent.resume(
                    decision.pending_task.interrupt_key, {"acknowledged": True}, lang=lang
                )
                st.rerun()
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
        st.subheader(i18n.ui_text(lang, "agent_trace"))
        for event in decision.trace:
            icon = "\U0001f6a8" if event.status == "override" else "\u26a0\ufe0f" if event.status == "degraded" else "\u2022"
            st.markdown(f"{icon} **{event.title}**")
            st.caption(event.detail)

        st.subheader(i18n.ui_text(lang, "tool_inspection"))
        for call in decision.tool_calls:
            with st.expander(f"{call.name} · {call.duration_ms} ms"):
                st.json(call.output)
else:
    st.info(i18n.ui_text(lang, "select_prompt"))
