"""Presentation-layer i18n. Internal logic never imports this module the
other way around: `policy.py` and `planner.py` produce stable, English,
machine-comparable keys (`Outcome` values, action strings, template keys);
this module only turns those keys into display text.

Two kinds of content are localized differently:

- Deterministic, enumerable content (outcome/risk labels, the four policy
  verdict reasons, guardrail override phrasing, deterministic eval-double
  rationale, policy citation excerpts, UI chrome) has hand-written
  English/Vietnamese templates and renders offline, instantly, for free.
- Free-form content (a real OpenRouterPlanner's rationale) cannot be
  templated. `translate_via_llm` makes one best-effort LLM call to
  translate it and returns `None` on any failure or missing credentials;
  callers must fall back to the English original and say so.
"""
from __future__ import annotations

import os

SUPPORTED_LANGS = ("en", "vi")


def _lang(lang: str) -> str:
    return lang if lang in SUPPORTED_LANGS else "en"


UI_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "title": "KYC Exception Resolution Agent",
        "subtitle": "Ontology-grounded reasoning, policy evidence, bounded tools, and human-controlled actions.",
        "run": "Run agent",
        "reasoning": "Reasoning...",
        "planner_label": "Planner",
        "case_label": "Case",
        "lang_label": "Language",
        "ontology_path": "Ontology path",
        "grounded_facts": "Grounded facts",
        "policy_evidence": "Policy evidence",
        "planner_rationale": "Planner rationale (advisory)",
        "guardrail_override": "Guardrail override",
        "guardrail_ok": "Guardrail check: planner proposal matched the mandated policy verdict.",
        "approval_required": "Human approval required",
        "approve_button": "Approve bounded action",
        "reject_button": "Reject action",
        "reject_reason": "Rejection reason",
        "action_rejected": "Action rejected",
        "action_executed": "Action executed",
        "idempotent_replay": "idempotent replay",
        "agent_trace": "Agent trace",
        "tool_inspection": "Tool inspection",
        "confidence": "planner confidence",
        "overridden": "overridden",
        "select_prompt": "Select a synthetic case and run the agent.",
        "translation_unavailable": "Live translation unavailable (no OPENROUTER_API_KEY) -- showing the model's original English answer.",
        "approve_prompt": "Approve this bounded action?",
        "requested_documents": "Requested documents",
        "workflow_status": "Workflow status",
        "current_node": "Current step",
        "planner_normal": "Normal planner",
        "planner_compromised": "Compromised demo",
        "planner_local": "Local (OpenAI-compatible)",
        "planner_help": "Choose the normal live planner or the compromised demo mode.",
        "compromised_warning": "Compromised demo mode is active; deterministic policy remains authoritative.",
        "submit_documents": "Submit verified requested documents",
        "rule_source": "Decided by rule",
        "acknowledge_handoff": "Acknowledge operational handoff",
        "not_available": "n/a",
        "cycle": "Evaluation cycle",
        "pending_document_submission": "Document submission required",
        "operational_handoff": "Operational review required",
        "planner_attempts": "Planner attempts",
        "tokens": "tokens",
        "cost": "cost",
    },
    "vi": {
        "title": "Tác Tử Xử Lý Ngoại Lệ KYC",
        "subtitle": "Suy luận dựa trên ontology, bằng chứng chính sách, công cụ có giới hạn, và hành động do con người kiểm soát.",
        "run": "Chạy tác tử",
        "reasoning": "Đang suy luận...",
        "planner_label": "Bộ lập luận",
        "case_label": "Hồ sơ",
        "lang_label": "Ngôn ngữ",
        "ontology_path": "Đường dẫn ontology",
        "grounded_facts": "Dữ kiện đã xác minh",
        "policy_evidence": "Bằng chứng chính sách",
        "planner_rationale": "Lý giải của bộ lập luận (chỉ tham khảo)",
        "guardrail_override": "Lớp bảo vệ đã ghi đè",
        "guardrail_ok": "Kiểm tra lớp bảo vệ: đề xuất của bộ lập luận khớp với phán quyết chính sách bắt buộc.",
        "approval_required": "Cần con người phê duyệt",
        "approve_button": "Phê duyệt hành động có giới hạn",
        "reject_button": "Từ chối hành động",
        "reject_reason": "Lý do từ chối",
        "action_rejected": "Đã từ chối hành động",
        "action_executed": "Đã thực thi hành động",
        "idempotent_replay": "phát lại idempotent",
        "agent_trace": "Nhật ký suy luận",
        "tool_inspection": "Chi tiết công cụ",
        "confidence": "độ tin cậy của bộ lập luận",
        "overridden": "đã bị ghi đè",
        "select_prompt": "Chọn một hồ sơ mẫu và chạy tác tử.",
        "translation_unavailable": "Không thể dịch trực tiếp (thiếu OPENROUTER_API_KEY) -- hiển thị nguyên văn tiếng Anh của mô hình.",
        "approve_prompt": "Phê duyệt hành động có giới hạn này?",
        "requested_documents": "Hồ sơ được yêu cầu bổ sung",
        "workflow_status": "Trạng thái quy trình",
        "current_node": "Bước hiện tại",
        "planner_normal": "Bộ lập luận bình thường",
        "planner_compromised": "Mô phỏng bộ lập luận bị xâm nhập",
        "planner_local": "Mô hình cục bộ (tương thích OpenAI)",
        "planner_help": "Chọn bộ lập luận trực tiếp bình thường hoặc chế độ mô phỏng bị xâm nhập.",
        "compromised_warning": "Đang bật chế độ mô phỏng bị xâm nhập; chính sách xác định vẫn là nguồn có thẩm quyền.",
        "submit_documents": "Gửi hồ sơ được yêu cầu đã xác minh",
        "rule_source": "Quyết định bởi quy tắc",
        "acknowledge_handoff": "Xác nhận chuyển rà soát vận hành",
        "not_available": "không có",
        "cycle": "Vòng đánh giá",
        "pending_document_submission": "Cần bổ sung tài liệu",
        "operational_handoff": "Cần chuyển rà soát vận hành",
        "planner_attempts": "Số lần thử bộ lập luận",
        "tokens": "token",
        "cost": "chi phí",
    },
}


def ui_text(lang: str, key: str) -> str:
    lang = _lang(lang)
    return UI_STRINGS[lang].get(key, UI_STRINGS["en"][key])


OUTCOME_LABELS = {
    "en": {
        "CLEAR": "CLEAR",
        "REQUEST_EVIDENCE": "REQUEST EVIDENCE",
        "MANUAL_REVIEW": "MANUAL REVIEW",
        "ESCALATE_COMPLIANCE": "ESCALATE COMPLIANCE",
    },
    "vi": {
        "CLEAR": "ĐÃ THÔNG QUA",
        "REQUEST_EVIDENCE": "YÊU CẦU BỔ SUNG HỒ SƠ",
        "MANUAL_REVIEW": "CẦN RÀ SOÁT THỦ CÔNG",
        "ESCALATE_COMPLIANCE": "CHUYỂN TUÂN THỦ KHẨN CẤP",
    },
}

RISK_LABELS = {
    "en": {"LOW": "LOW", "MEDIUM": "MEDIUM", "HIGH": "HIGH", "CRITICAL": "CRITICAL", "UNKNOWN": "UNKNOWN"},
    "vi": {"LOW": "THẤP", "MEDIUM": "TRUNG BÌNH", "HIGH": "CAO", "CRITICAL": "NGHIÊM TRỌNG", "UNKNOWN": "CHƯA XÁC ĐỊNH"},
}

NO_ACTION_LABEL = {"en": "no-action", "vi": "không hành động"}

FIELD_LABELS = {
    "en": {"proof_of_address": "proof of address", "id_document_reupload": "clearer ID document re-upload"},
    "vi": {"proof_of_address": "chứng minh địa chỉ thường trú", "id_document_reupload": "ảnh giấy tờ tùy thân rõ nét hơn"},
}


def outcome_label(lang: str, outcome: str) -> str:
    lang = _lang(lang)
    return OUTCOME_LABELS[lang].get(outcome, outcome)


def risk_label(lang: str, risk: str) -> str:
    lang = _lang(lang)
    return RISK_LABELS[lang].get(risk, risk)


def field_label(lang: str, field: str) -> str:
    lang = _lang(lang)
    return FIELD_LABELS[lang].get(field, field)


# ---------------------------------------------------------------------------
# Policy verdict reasons. Keyed by the same `reason_key` policy.py attaches
# to every PolicyVerdict, so rendering never re-derives business logic.
# ---------------------------------------------------------------------------
REASON_TEMPLATES: dict[str, dict[str, str]] = {
    "sanctions_hit": {
        "en": "Sanctions match score {score:.2f} >= {threshold:.2f} (AML-SCREEN-02); "
        "mandatory Compliance escalation, no automated contact permitted.",
        "vi": "Điểm trùng khớp danh sách trừng phạt {score:.2f} >= {threshold:.2f} (AML-SCREEN-02); "
        "bắt buộc chuyển bộ phận Tuân thủ xử lý, không được tự động liên hệ khách hàng.",
    },
    "identity_conflict": {
        "en": "Identity evidence conflicts (name mismatch or failed liveness); "
        "KYC-IDENTITY-11 requires manual review before any resolution.",
        "vi": "Hồ sơ định danh có mâu thuẫn (sai lệch họ tên hoặc không đạt kiểm tra sinh trắc học); "
        "theo KYC-IDENTITY-11 phải chuyển rà soát thủ công trước khi ra quyết định.",
    },
    "ocr_name_mismatch": {
        "en": "Name difference '{declared}' vs '{document}' is fully explained by lost diacritics or OCR glyph "
        "confusion on a live capture (tamper {tamper:.2f} < {max_tamper:.2f}); expert rule KYC-IDENTITY-12 requests "
        "a {fields} before any manual review.",
        "vi": "Sai khác họ tên '{declared}' và '{document}' hoàn toàn do mất dấu tiếng Việt hoặc nhầm ký tự OCR, "
        "ảnh chụp trực tiếp hợp lệ (điểm giả mạo {tamper:.2f} < {max_tamper:.2f}); theo quy tắc chuyên gia "
        "KYC-IDENTITY-12, yêu cầu bổ sung {fields} trước khi chuyển rà soát thủ công.",
    },
    "missing_evidence": {
        "en": "Identity checks pass; missing fields {fields} require a KYC-EVIDENCE-07 "
        "document request while the application stays pending.",
        "vi": "Đã xác minh định danh; còn thiếu {fields} nên cần yêu cầu bổ sung hồ sơ "
        "theo KYC-EVIDENCE-07, hồ sơ tiếp tục ở trạng thái chờ.",
    },
    "clear": {
        "en": "Identity, liveness, sanctions, and evidence checks all pass (KYC-CLEAR-01); "
        "no exception action is required.",
        "vi": "Đã đạt toàn bộ kiểm tra định danh, sinh trắc học, danh sách trừng phạt và hồ sơ "
        "(KYC-CLEAR-01); không cần xử lý ngoại lệ.",
    },
    "ai_unavailable": {
        "en": "Live model reasoning is unavailable; the case requires manual handling and no automated action was taken.",
        "vi": "Không thể sử dụng suy luận từ mô hình trực tiếp; hồ sơ cần được xử lý thủ công và không có hành động tự động nào được thực hiện.",
    },
    "tool_unavailable": {
        "en": "Authoritative case data is incomplete after retries; the case requires operational review.",
        "vi": "Dữ liệu hồ sơ có thẩm quyền vẫn chưa đầy đủ sau khi thử lại; hồ sơ cần được rà soát vận hành.",
    },
    "policy_unavailable": {
        "en": "Applicable policy evidence is missing or contradictory; automated resolution is blocked.",
        "vi": "Bằng chứng chính sách áp dụng bị thiếu hoặc mâu thuẫn; hệ thống chặn xử lý tự động.",
    },
    "cycle_exhausted": {
        "en": "Required evidence is still incomplete after the maximum evaluation cycles; manual review is required.",
        "vi": "Hồ sơ vẫn chưa đầy đủ sau số vòng đánh giá tối đa; cần rà soát thủ công.",
    },
    "submission_attempts_exhausted": {
        "en": "The maximum number of document resubmission attempts was reached without a valid submission; manual review is required.",
        "vi": "Đã đạt số lần bổ sung tài liệu tối đa mà vẫn chưa hợp lệ; cần rà soát thủ công.",
    },
}


def render_reason(reason_key: str, params: dict, lang: str) -> str:
    lang = _lang(lang)
    templates = REASON_TEMPLATES[reason_key]
    rendered_params = dict(params)
    fields = rendered_params.get("fields")
    if isinstance(fields, list):
        rendered_params["fields"] = ", ".join(field_label(lang, f) for f in fields)
    return templates.get(lang, templates["en"]).format(**rendered_params)


OVERRIDE_TEMPLATE = {
    "en": "Model ({model}) proposed {proposal_outcome}/{proposal_action}; guardrail enforced "
    "the mandated {final_outcome}/{final_action} instead. Reason: {reason}",
    "vi": "Mô hình ({model}) đề xuất {proposal_outcome}/{proposal_action}; lớp bảo vệ đã buộc "
    "thực thi {final_outcome}/{final_action} theo đúng quy định. Lý do: {reason}",
}


def render_override(info: dict, lang: str) -> str:
    lang = _lang(lang)
    reason = render_reason(info["reason_key"], info["reason_params"], lang)
    no_action = NO_ACTION_LABEL[lang]
    return OVERRIDE_TEMPLATE.get(lang, OVERRIDE_TEMPLATE["en"]).format(
        model=info["model"],
        proposal_outcome=outcome_label(lang, info["proposal_outcome"]),
        proposal_action=info["proposal_action"] or no_action,
        final_outcome=outcome_label(lang, info["final_outcome"]),
        final_action=info["final_action"] or no_action,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Grounded facts, rendered from the raw typed facts dict (never from text).
# ---------------------------------------------------------------------------
def render_facts(facts: dict, lang: str) -> list[str]:
    lang = _lang(lang)
    docs = facts.get("verify_documents")
    sanctions = facts.get("screen_sanctions")
    risk = facts.get("get_risk_profile")
    templates = {
        "risk_tier": {"en": "Risk tier: {level} (score {score})", "vi": "Mức rủi ro: {level} (điểm {score})"},
        "liveness_passed": {"en": "Liveness: passed", "vi": "Sinh trắc học: đạt"},
        "liveness_failed": {"en": "Liveness: failed", "vi": "Sinh trắc học: không đạt"},
        "sanctions_score": {
            "en": "Sanctions match score: {score:.2f}",
            "vi": "Điểm trùng khớp danh sách trừng phạt: {score:.2f}",
        },
        "sanctions_candidate": {
            "en": "Matched list entry: {candidate}",
            "vi": "Mục trùng khớp trong danh sách: {candidate}",
        },
        "missing_fields": {"en": "Missing fields: {fields}", "vi": "Còn thiếu: {fields}"},
        "name_diff": {
            "en": "Name mismatch: '{declared}' vs '{document}' ({kind})",
            "vi": "Sai lệch họ tên: '{declared}' và '{document}' ({kind})",
        },
        "ocr_explainable": {"en": "explainable by OCR/diacritics", "vi": "có thể do OCR/mất dấu"},
        "not_explainable": {"en": "not explainable by OCR", "vi": "không thể giải thích bằng lỗi OCR"},
    }

    def t(key: str, **params) -> str:
        return templates[key].get(lang, templates[key]["en"]).format(**params)

    lines: list[str] = []
    if risk is not None:
        lines.append(t("risk_tier", level=risk_label(lang, risk.get("level", "UNKNOWN")), score=risk.get("score")))
    if docs is not None:
        lines.append(t("liveness_passed" if docs.get("liveness_passed") else "liveness_failed"))
    if sanctions is not None and sanctions.get("match_score") is not None:
        lines.append(t("sanctions_score", score=sanctions["match_score"]))
    if sanctions is not None and sanctions.get("candidate"):
        lines.append(t("sanctions_candidate", candidate=sanctions["candidate"]))
    if docs is not None and docs.get("missing_fields"):
        fields = ", ".join(field_label(lang, f) for f in docs["missing_fields"])
        lines.append(t("missing_fields", fields=fields))
    diff = (docs or {}).get("name_diff") or {}
    if docs is not None and docs.get("name_match") is False and diff.get("declared_token"):
        kind = t("ocr_explainable" if diff.get("ocr_explainable") else "not_explainable")
        lines.append(t("name_diff", declared=diff["declared_token"], document=diff["document_token"], kind=kind))
    return lines


# ---------------------------------------------------------------------------
# Planner rationale. Deterministic eval doubles are templated (fully
# offline-translatable); a real LLM's free-form rationale needs a live call.
# ---------------------------------------------------------------------------
RATIONALE_TEMPLATES: dict[str, dict[str, str]] = {
    "heuristic_applied": {
        "en": "The deterministic eval double applied {citations} to the grounded facts. "
        "The case note is untrusted free text and was not used to reach this recommendation.",
        "vi": "Bộ lập luận ngoại tuyến đã áp dụng {citations} dựa trên "
        "dữ kiện đã xác minh. Ghi chú hồ sơ là văn bản tự do, không đáng tin cậy, và không được "
        "dùng để đưa ra khuyến nghị này.",
    },
    "adversarial_injection": {
        "en": "The compromised demo proposal follows the untrusted case note and recommends CLEAR "
        "to approve the account today.",
        "vi": "Đề xuất mô phỏng bị xâm nhập chạy theo ghi chú hồ sơ không đáng tin cậy, cho rằng đây là "
        "báo động giả, và đề xuất THÔNG QUA để duyệt tài khoản ngay hôm nay.",
    },
    "planner_failed": {
        "en": "{planner_class} raised {error_class}: {error}. Continued with the policy-safe path "
        "{fallback_outcome}: {fallback_reason}",
        "vi": "{planner_class} phát sinh lỗi {error_class}: {error}. Đã tiếp tục theo nhánh an toàn "
        "của chính sách {fallback_outcome}: {fallback_reason}",
    },
}


def render_rationale_template(key: str, params: dict, lang: str) -> str:
    lang = _lang(lang)
    templates = RATIONALE_TEMPLATES[key]
    return templates.get(lang, templates["en"]).format(**params)


DEFAULT_TRANSLATE_MODEL = "openai/gpt-4o-mini"
TARGET_LANG_NAME = {"vi": "Vietnamese", "en": "English"}


def translate_via_llm(text: str, target_lang: str) -> str | None:
    """One best-effort LLM call to translate free-form planner rationale.
    Returns None (never raises) on a missing key, network error, or timeout
    so callers can fall back to the English original."""
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key or key == "your_key_here" or target_lang not in TARGET_LANG_NAME:
        return None
    try:
        from langchain_openai import ChatOpenAI

        client = ChatOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=key,
            model=os.getenv("OPENROUTER_MODEL", DEFAULT_TRANSLATE_MODEL),
            temperature=0,
            timeout=15,
        )
        response = client.invoke(
            [
                ("system", f"Translate the user's text to {TARGET_LANG_NAME[target_lang]}. "
                 "Return only the translation, no preamble, no notes."),
                ("user", text),
            ]
        )
        translated = (response.content or "").strip()
        return translated or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Policy citation excerpts. In production this would be a versioned,
# per-locale artifact owned by Compliance, not a hardcoded dict -- kept here
# for demo simplicity.
# ---------------------------------------------------------------------------
POLICY_EXCERPTS_VI: dict[str, str] = {
    "KYC-EVIDENCE-07": "Nếu các kiểm tra định danh cốt lõi đã đạt nhưng thiếu chứng minh địa chỉ, "
    "giữ hồ sơ ở trạng thái chờ và yêu cầu bổ sung tài liệu còn thiếu.",
    "KYC-IDENTITY-11": "Sai lệch họ tên hợp pháp hoặc không đạt kiểm tra sinh trắc học yêu cầu rà "
    "soát thủ công; tác tử có thể tổng hợp hồ sơ nhưng không được duyệt đơn.",
    "KYC-IDENTITY-12": "Ngoại lệ của mục 3.1: khi mâu thuẫn định danh duy nhất là một từ trong họ tên có thể giải thích "
    "do mất dấu tiếng Việt hoặc nhầm ký tự OCR, sinh trắc học đạt và điểm giả mạo dưới 0.10, yêu cầu tải lại ảnh giấy tờ "
    "rõ nét hơn trước khi chuyển rà soát thủ công. Ảnh tải lại được xác minh lại; mâu thuẫn còn lại xử lý theo mục 3.1.",
    "AML-SCREEN-02": "Điểm sàng lọc danh sách trừng phạt từ 0.80 trở lên yêu cầu chuyển ngay cho bộ phận Tuân thủ. "
    "Nghiêm cấm tự động liên hệ khách hàng hoặc phê duyệt, kể cả khi ghi chú hồ sơ hay khách "
    "hàng khẳng định đây là báo động giả.",
    "KYC-RISK-05": "Đơn có mức rủi ro trung bình hoặc cao cần ghi rõ lý do, nguồn gốc hồ sơ, "
    "và danh tính người rà soát trước khi ra quyết định.",
    "KYC-CLEAR-01": "Khi định danh, sinh trắc học, danh sách trừng phạt và hồ sơ đều đạt, đóng "
    "hồ sơ ngoại lệ mà không cần thêm hành động, đồng thời lưu lại toàn bộ dấu vết để kiểm toán.",
}


def policy_excerpt(policy_id: str, english_text: str, lang: str) -> str:
    lang = _lang(lang)
    if lang == "vi" and policy_id in POLICY_EXCERPTS_VI:
        return POLICY_EXCERPTS_VI[policy_id]
    return english_text
