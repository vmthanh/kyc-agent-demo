"""Reflect: evidence-gated ontology change (Dana Assurance-style).

Reviewer signals (a rejected action, an operational handoff) become *candidate*
rule amendments. A candidate is never trusted because of who or what drafted
it: it must (1) pass the ontology loader's safety validation, (2) be replayed
against the labelled golden set and the shipped cases, and (3) be approved by
people with authority before it is activated. Nothing here self-activates.

    signal -> draft (rules or LLM) -> validate -> replay -> approve -> promote
"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import ontology as ontology_module
from .authority import check_approval, requirement, satisfied
from .ontology import Ontology, OntologyError, load_ontology
from .tools import DomainTools

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "golden" / "cases.json"
SHIPPED = ROOT / "data" / "cases.json"
DEFAULT_STORE = ROOT / ".runtime" / "ontology"
READ_TOOLS = ("get_case", "verify_documents", "screen_sanctions", "get_risk_profile")
RESTRICTIVENESS = {"CLEAR": 0, "REQUEST_EVIDENCE": 1, "MANUAL_REVIEW": 2, "ESCALATE_COMPLIANCE": 3}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _bump(version: str) -> str:
    major, _, minor = str(version).partition(".")
    return f"{major}.{int(minor or 0) + 1}"


# --------------------------------------------------------------------- data
@dataclass
class ReviewSignal:
    id: str
    kind: str  # "rejection" | "operational_handoff"
    case_id: str
    run_id: str
    rule_id: str | None
    rule_version: str | None
    outcome: str | None
    action_payload: dict[str, Any] | None
    reviewer_reason: str
    facts: dict[str, Any]
    created_at: str = field(default_factory=_now)


@dataclass
class Amendment:
    id: str
    rule_id: str
    base_version: str | None
    candidate: dict[str, Any] | None
    drafter: str
    author: str
    signal_id: str | None = None
    diff: list[dict[str, Any]] = field(default_factory=list)
    impact: dict[str, Any] | None = None
    status: str = "proposed"  # proposed | blocked | invalid | promoted | rejected
    errors: list[str] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    promoted_ontology_version: str | None = None
    created_at: str = field(default_factory=_now)


# ------------------------------------------------------------------ drafters
class Drafter(Protocol):
    name: str

    def draft(self, rule: dict[str, Any], signal: ReviewSignal) -> dict[str, Any]: ...


class NarrowingDrafter:
    """Deterministic drafter: tighten one numeric bound just enough that the
    rejected case no longer matches. Never touches hard-stop rules -- a reviewer
    disagreeing with a hard stop is a Compliance conversation, not a rule edit."""

    name = "rules:narrowing"

    def draft(self, rule: dict[str, Any], signal: ReviewSignal) -> dict[str, Any]:
        if rule.get("hard_stop"):
            raise OntologyError("hard-stop rules cannot be amended from a reviewer signal")
        candidate = copy.deepcopy(rule)
        for leaf in _leaves(candidate["when"]):
            value = leaf.get("value")
            if not (isinstance(value, str) and value.startswith("@")):
                continue
            param = value[1:]
            try:
                observed = _fact(signal.facts, leaf["fact"])
            except KeyError:
                continue
            if not isinstance(observed, (int, float)) or isinstance(observed, bool):
                continue
            current = candidate["params"][param]
            if leaf["op"] in ("<", "<=") and observed <= current:
                # `fact < p` matched with fact=observed; p=observed excludes it for `<`.
                candidate["params"][param] = observed if leaf["op"] == "<" else round(observed - 0.01, 4)
                return candidate
            if leaf["op"] in (">", ">=") and observed >= current:
                candidate["params"][param] = observed if leaf["op"] == ">" else round(observed + 0.01, 4)
                return candidate
        raise OntologyError(f"no numeric bound in {rule['id']} can be narrowed to exclude {signal.case_id}; draft manually")


class LLMDrafter:
    """Asks an LLM to propose a narrowed rule. Its output is untrusted: it goes
    through exactly the same validation, replay, and approval as any draft."""

    name = "llm"

    def __init__(self, model: str | None = None) -> None:
        import os

        from langchain_openai import ChatOpenAI

        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not key or key == "your_key_here":
            raise ValueError("OPENROUTER_API_KEY is required for the LLM drafter")
        self.model = model or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        self.name = f"llm:{self.model}"
        self._client = ChatOpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, model=self.model,
                                  temperature=0, timeout=30)

    def draft(self, rule: dict[str, Any], signal: ReviewSignal) -> dict[str, Any]:
        system = (
            "You maintain an executable KYC rule ontology. Propose a minimal amendment to ONE rule so that it "
            "reflects the reviewer's feedback. Keep the same rule id and JSON structure. You may change `params`, "
            "or add conditions to `when` using only these operators: == != >= > <= < in not_empty empty is_true "
            "is_false, with `fact` paths rooted in get_case, verify_documents, screen_sanctions, get_risk_profile. "
            "Never reference case_note. Return ONLY the full rule as a JSON object, no prose."
        )
        user = json.dumps({"current_rule": rule, "reviewer_reason": signal.reviewer_reason,
                           "case_facts": signal.facts}, ensure_ascii=False, default=str)
        text = str(self._client.invoke([("system", system), ("user", user)]).content).strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[1].rsplit("```", 1)[0] if "\n" in text else text.strip("`")
        try:
            candidate = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OntologyError(f"LLM draft is not valid JSON: {exc}") from exc
        if not isinstance(candidate, dict):
            raise OntologyError("LLM draft must be a JSON object")
        return candidate


# -------------------------------------------------------------- validation
def _leaves(cond: dict[str, Any]):
    for key in ("all", "any"):
        if key in cond:
            for sub in cond[key]:
                yield from _leaves(sub)
            return
    if "not" in cond:
        yield from _leaves(cond["not"])
        return
    yield cond


def _fact(facts: dict[str, Any], path: str) -> Any:
    node: Any = facts
    for part in path.split("."):
        node = node[part]
    return node


def _diff(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            out += _diff(before.get(key), after.get(key), f"{path}.{key}" if path else key)
        return out
    return [] if before == after else [{"path": path, "before": before, "after": after}]


def candidate_ontology(base_raw: dict[str, Any], candidate_rule: dict[str, Any]) -> tuple[dict[str, Any], Ontology]:
    """Splice the candidate (as active) into a copy of the ontology and validate the whole thing."""
    raw = copy.deepcopy(base_raw)
    rule = copy.deepcopy(candidate_rule)
    rule["status"] = "active"
    rules = raw["cognitive"]["rules"]
    for i, existing in enumerate(rules):
        if existing["id"] == rule["id"]:
            rules[i] = rule
            break
    else:
        rules.append(rule)
    raw["version"] = _bump(raw["version"])
    return raw, Ontology.from_dict(raw)


# ------------------------------------------------------------------- replay
def replay(current: Ontology, candidate: Ontology, sources: list[tuple[str, Path]] | None = None) -> dict[str, Any]:
    """Re-decide every golden and shipped case under both ontologies (pure: no model, no writes)."""
    sources = sources or [("golden", GOLDEN), ("shipped", SHIPPED)]
    changed, regressions, loosened, tightened = [], [], 0, 0
    agree_before = agree_after = labelled = total = 0
    for label, path in sources:
        tools = DomainTools(path)
        for case in tools.cases:
            facts = {name: tools.call(name, "replay", {"case_id": case["case_id"]}).output for name in READ_TOOLS}
            before, after = current.evaluate(facts), candidate.evaluate(facts)
            expected = (case.get("expected") or {}).get("outcome")
            total += 1
            if expected:
                labelled += 1
                agree_before += before.outcome.value == expected
                agree_after += after.outcome.value == expected
            if (before.outcome, before.rule.id) == (after.outcome, after.rule.id):
                continue
            b, a = RESTRICTIVENESS[before.outcome.value], RESTRICTIVENESS[after.outcome.value]
            loosened += a < b
            tightened += a > b
            entry = {"source": label, "case_id": case["case_id"], "expected": expected,
                     "before": f"{before.outcome.value} ({before.rule.ref})", "after": f"{after.outcome.value} ({after.rule.ref})"}
            changed.append(entry)
            # A safety regression: the new decision is less restrictive than the mandate
            # (labelled cases) or than today's decision on an unlabelled shipped case.
            floor = RESTRICTIVENESS[expected] if expected else b
            if a < floor and a < b:
                regressions.append(entry)
    return {
        "cases_replayed": total,
        "changed": changed,
        "tightened": tightened,
        "loosened": loosened,
        "safety_regressions": regressions,
        "agreement_before": round(agree_before / labelled, 3) if labelled else None,
        "agreement_after": round(agree_after / labelled, 3) if labelled else None,
        "label_updates_needed": [c["case_id"] for c in changed if c["expected"] and c not in regressions
                                 and not c["after"].startswith(c["expected"])],
    }


# -------------------------------------------------------------------- store
class ReflectStore:
    """Signals, amendments, and the promotion gate. In-memory with an append-only
    audit log; promoted ontologies are written under `store_dir`, never over the
    shipped `data/ontology.json`."""

    def __init__(self, store_dir: Path | None = None, replay_sources: list[tuple[str, Path]] | None = None) -> None:
        self.store_dir = Path(store_dir or DEFAULT_STORE)
        self.replay_sources = replay_sources
        self.signals: dict[str, ReviewSignal] = {}
        self.amendments: dict[str, Amendment] = {}
        self._lock = threading.RLock()

    # ---- signals
    def record_signal(self, **fields: Any) -> ReviewSignal:
        with self._lock:
            for existing in self.signals.values():
                if (existing.run_id, existing.kind) == (fields["run_id"], fields["kind"]):
                    return existing
            signal = ReviewSignal(id=f"SIG-{len(self.signals) + 1:03d}", **fields)
            self.signals[signal.id] = signal
            self._audit("signal_recorded", {"signal": asdict(signal)})
            return signal

    # ---- amendments
    def propose(self, *, signal_id: str | None = None, rule: dict[str, Any] | None = None,
                drafter: Drafter | None = None, author: str = "fde") -> Amendment:
        with self._lock:
            current = load_ontology()
            base_raw = current.raw
            signal = self.signals.get(signal_id) if signal_id else None
            if signal_id and signal is None:
                raise KeyError(f"Unknown or expired signal: {signal_id}")
            if rule is None:
                if signal is None or not signal.rule_id:
                    raise ValueError("provide a signal with a deciding rule, or a candidate rule")
                target = copy.deepcopy(next(r for r in base_raw["cognitive"]["rules"] if r["id"] == signal.rule_id))
                drafter = drafter or NarrowingDrafter()
                drafter_name = drafter.name
            else:
                target = next((copy.deepcopy(r) for r in base_raw["cognitive"]["rules"] if r["id"] == rule.get("id")), {})
                drafter_name = "manual"
            amendment = Amendment(id=f"AMD-{uuid.uuid4().hex[:6]}", rule_id=str((rule or target).get("id")),
                                  base_version=target.get("version"), candidate=None, drafter=drafter_name,
                                  author=author, signal_id=signal_id)
            try:
                candidate = rule if rule is not None else drafter.draft(target, signal)
                candidate = copy.deepcopy(candidate)
                candidate["id"] = amendment.rule_id
                candidate["version"] = _bump(target.get("version", "1.0")) if target else candidate.get("version", "1.0")
                candidate["status"] = "candidate"
                candidate.setdefault("source", {})
                candidate["source"] = {**candidate["source"], "amended_from": f"{amendment.rule_id}@{target.get('version')}" if target else None,
                                       "amendment": amendment.id, "signal": signal_id}
                amendment.candidate = candidate
                amendment.diff = _diff({k: v for k, v in target.items() if k not in ("source", "status")},
                                       {k: v for k, v in candidate.items() if k not in ("source", "status")})
                _, candidate_onto = candidate_ontology(base_raw, candidate)
                amendment.impact = replay(current, candidate_onto, self.replay_sources)
                if amendment.impact["safety_regressions"]:
                    amendment.status = "blocked"
                    amendment.errors.append(f"{len(amendment.impact['safety_regressions'])} safety regression(s) on replay")
            except OntologyError as exc:
                amendment.status = "invalid"
                amendment.errors.append(str(exc))
            self.amendments[amendment.id] = amendment
            self._audit("amendment_proposed", {"amendment": asdict(amendment)})
            return amendment

    def approve(self, amendment_id: str, approver: str, role: str, approved: bool = True, reason: str = "") -> Amendment:
        with self._lock:
            amendment = self.amendments.get(amendment_id)
            if amendment is None:
                raise KeyError(f"Unknown or expired amendment: {amendment_id}")
            if amendment.status != "proposed":
                raise ValueError(f"amendment is {amendment.status}; only proposed amendments can be approved")
            req = self.requirement()
            granted = [a for a in amendment.approvals if a["approved"]]
            if approved:
                checked = check_approval(req, granted, {"approver": approver, "role": role})
            elif not approver.strip():
                raise ValueError("approver is required")
            else:
                checked = {"approver": approver.strip(), "role": role}
            amendment.approvals.append({**checked, "approved": approved, "reason": reason, "at": _now()})
            if not approved:
                amendment.status = "rejected"
            elif satisfied(req, [a for a in amendment.approvals if a["approved"]]):
                self._promote(amendment)
            self._audit("amendment_reviewed", {"amendment": amendment.id, "approver": approver, "role": role, "approved": approved})
            return amendment

    @staticmethod
    def requirement() -> dict[str, Any]:
        """Who may promote an ontology change, from the *active* ontology's authority matrix."""
        return requirement(load_ontology().authority, "promote_amendment").to_payload()

    def _promote(self, amendment: Amendment) -> None:
        current = load_ontology()
        raw, onto = candidate_ontology(current.raw, amendment.candidate)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        path = self.store_dir / f"ontology-v{raw['version']}.json"
        path.write_text(json.dumps(raw, indent=2, ensure_ascii=False))
        ontology_module.activate(path)
        amendment.status = "promoted"
        amendment.promoted_ontology_version = onto.version
        self._audit("ontology_promoted", {"amendment": amendment.id, "version": onto.version, "path": str(path)})

    def reset(self) -> None:
        with self._lock:
            ontology_module.reset_active()
            self._audit("ontology_reset", {"version": load_ontology().version})

    def _audit(self, event: str, payload: dict[str, Any]) -> None:
        try:
            self.store_dir.mkdir(parents=True, exist_ok=True)
            with (self.store_dir / "audit.jsonl").open("a") as fh:
                fh.write(json.dumps({"at": _now(), "event": event, **payload}, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass  # the audit log is best-effort in the demo; production would fail closed
