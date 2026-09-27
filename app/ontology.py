"""Executable Cognitive Ontology.

`data/ontology.json` has two layers:

- `structural` -- *what exists*: entities and relations (Customer, Evidence, ...).
- `cognitive`  -- *what to do about it*: versioned, typed rules that decide a
  KYC exception's mandated outcome. Domain experts change these rules; the
  interpreter below never changes per rule.

The rule language is deliberately closed: a fixed operator set, `all`/`any`/
`not` combinators, dotted paths into the four grounded fact sources only, and
no `eval`. A rule that reads `case_note` (untrusted free text) or anything
outside the grounded sources is rejected at load time, so prompt-injected text
can never become a rule input.

Loader-enforced safety properties:

- exactly one always-true fallback rule, evaluated last;
- `hard_stop` rules carry no write action and cannot be shadowed by any
  higher-priority non-hard-stop rule;
- outcomes and actions come from closed allowlists;
- a missing fact fails closed (`FactMissingError`) unless a leaf opts in with
  `"missing": "false"`, which makes that leaf simply not hold. That opt-in is
  rejected under `not`, so absent evidence can never *enable* a rule;
- only `status: "active"` rules are evaluated (`candidate`/`retired` rules are
  still validated, so a drafted amendment is checked before anyone reviews it).

This module is pure (no network, no model, no display language). It returns a
`RuleMatch`; `app/policy.py` turns that into the `PolicyVerdict` the workflow uses.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .domain import Outcome

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "ontology.json"
ONTOLOGY_PATH_ENV = "KYC_ONTOLOGY_PATH"

COMPARISON_OPS = {"==", "!=", ">=", ">", "<=", "<", "in"}
UNARY_OPS = {"not_empty", "empty", "is_true", "is_false"}
RULE_STATUSES = {"active", "candidate", "retired"}
UNTRUSTED_FIELDS = {"case_note"}
LEAF_KEYS = {"fact", "op", "value", "missing"}
MISSING_POLICIES = {"error", "false"}


class OntologyError(ValueError):
    """The ontology document is invalid; it must never be partially loaded."""


class FactMissingError(KeyError):
    """A rule referenced a grounded fact that is absent: fail closed, never guess."""


@dataclass(frozen=True)
class Rule:
    id: str
    version: str
    status: str
    priority: int
    hard_stop: bool
    when: dict[str, Any]
    then: dict[str, Any]
    cites: str
    retrieval_tags: tuple[str, ...]
    ontology_path: tuple[str, ...]
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    source: dict[str, Any] = field(default_factory=dict)

    @property
    def is_fallback(self) -> bool:
        return self.when == {"all": []}

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


@dataclass(frozen=True)
class RuleMatch:
    rule: Rule
    outcome: Outcome
    action: str | None
    risk_level: str
    reason_key: str
    reason_params: dict[str, Any]


class Ontology:
    def __init__(
        self,
        version: str,
        entities: dict[str, list[str]],
        relations: list[list[str]],
        fact_sources: tuple[str, ...],
        actions: tuple[str, ...],
        base_retrieval_tags: tuple[str, ...],
        all_rules: tuple[Rule, ...],
    ) -> None:
        self.version = version
        self.entities = entities
        self.relations = relations
        self.fact_sources = fact_sources
        self.actions = actions
        self.base_retrieval_tags = base_retrieval_tags
        self.all_rules = all_rules
        self.rules = tuple(sorted((r for r in all_rules if r.status == "active"), key=lambda r: r.priority))
        self.raw: dict[str, Any] = {}

    # ------------------------------------------------------------------ load
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Ontology":
        try:
            structural = data["structural"]
            cognitive = data["cognitive"]
            entities = dict(structural["entities"])
            relations = [list(r) for r in structural["relations"]]
            fact_sources = tuple(cognitive["fact_sources"])
            actions = tuple(cognitive["actions"])
            base_tags = tuple(cognitive.get("base_retrieval_tags", []))
            raw_rules = list(cognitive["rules"])
            version = str(data["version"])
        except (KeyError, TypeError) as exc:
            raise OntologyError(f"ontology is missing a required section: {exc}") from exc
        if set(fact_sources) & UNTRUSTED_FIELDS:
            raise OntologyError("case_note is untrusted and cannot be a fact source")

        rules: list[Rule] = []
        seen: set[str] = set()
        for raw in raw_rules:
            r = _parse_rule(raw, entities, fact_sources, actions)
            if r.id in seen:
                raise OntologyError(f"duplicate rule id: {r.id}")
            seen.add(r.id)
            rules.append(r)

        onto = cls(version, entities, relations, fact_sources, actions, base_tags, tuple(rules))
        _check_active_set(onto.rules)
        onto.raw = copy.deepcopy(data)
        return onto

    # -------------------------------------------------------------- evaluate
    def evaluate(self, facts: dict[str, Any]) -> RuleMatch:
        """First active rule (by ascending priority) whose condition holds."""
        for r in self.rules:
            if _holds(r.when, facts, r.params):
                return _apply(r, facts)
        raise OntologyError("no rule matched; the fallback rule is missing")  # unreachable after validation

    def tags_for(self, facts: dict[str, Any]) -> set[str]:
        return set(self.base_retrieval_tags) | set(self.evaluate(facts).rule.retrieval_tags)

    def rule(self, rule_id: str) -> Rule:
        for r in self.all_rules:
            if r.id == rule_id:
                return r
        raise KeyError(rule_id)

    def required_policy(self, reason_key: str) -> str | None:
        for r in self.rules:
            if r.then.get("reason_key") == reason_key:
                return r.cites
        return None

    def rule_view(self, rule_id: str | None, rule_version: str | None = None) -> dict[str, Any] | None:
        """Audit-friendly attribution of the deciding rule, or None if no rule decided."""
        if not rule_id:
            return None
        try:
            r = self.rule(rule_id)
        except KeyError:
            return {"id": rule_id, "version": rule_version, "cites": None, "source": {}, "description": ""}
        return {"id": r.id, "version": rule_version or r.version, "cites": r.cites,
                "source": dict(r.source), "description": r.description}

    def path_for(self, rule_id: str | None, rule_version: str | None, outcome: str) -> list[str]:
        """Human-readable lineage: entities the rule reasons over -> rule -> policy -> resolution."""
        try:
            r = self.rule(rule_id) if rule_id else None
        except KeyError:
            r = None
        if r is None:
            return ["Customer", "KYCApplication", "Evidence", "RiskFinding", "Policy", f"Resolution {outcome}"]
        return [*r.ontology_path, f"Rule {r.id}@{rule_version or r.version}", f"Policy {r.cites}", f"Resolution {outcome}"]


# ---------------------------------------------------------------- validation
def _parse_rule(raw: dict[str, Any], entities: dict, fact_sources: tuple[str, ...], actions: tuple[str, ...]) -> Rule:
    try:
        rule_id = str(raw["id"])
        r = Rule(
            id=rule_id,
            version=str(raw["version"]),
            status=str(raw.get("status", "active")),
            priority=int(raw["priority"]),
            hard_stop=bool(raw.get("hard_stop", False)),
            when=raw["when"],
            then=dict(raw["then"]),
            cites=str(raw["cites"]),
            retrieval_tags=tuple(raw.get("retrieval_tags", [])),
            ontology_path=tuple(raw.get("ontology_path", [])),
            params=dict(raw.get("params", {})),
            description=str(raw.get("description", "")),
            source=dict(raw.get("source", {})),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OntologyError(f"rule {raw.get('id', '?')}: malformed ({exc})") from exc

    where = f"rule {r.id}"
    if r.status not in RULE_STATUSES:
        raise OntologyError(f"{where}: unknown status {r.status!r}")
    _check_condition(r.when, r, fact_sources, where)

    then = r.then
    for key in ("outcome", "action", "risk_level", "reason_key", "reason_params"):
        if key not in then:
            raise OntologyError(f"{where}: then.{key} is required")
    if then["outcome"] not in {o.value for o in Outcome}:
        raise OntologyError(f"{where}: unknown outcome {then['outcome']!r}")
    if then["action"] is not None and then["action"] not in actions:
        raise OntologyError(f"{where}: action {then['action']!r} is not in the ontology action allowlist")
    if r.hard_stop and then["action"] is not None:
        raise OntologyError(f"{where}: a hard_stop rule must not carry a write action")
    if not isinstance(then["reason_params"], dict):
        raise OntologyError(f"{where}: then.reason_params must be an object")
    for value in [then["risk_level"], *then["reason_params"].values()]:
        _check_value_ref(value, r, fact_sources, where)
    for entity in r.ontology_path:
        if entity not in entities:
            raise OntologyError(f"{where}: ontology_path references unknown entity {entity!r}")
    return r


def _check_condition(cond: Any, r: Rule, fact_sources: tuple[str, ...], where: str, negated: bool = False) -> None:
    if not isinstance(cond, dict):
        raise OntologyError(f"{where}: condition must be an object")
    if "all" in cond or "any" in cond:
        key = "all" if "all" in cond else "any"
        if len(cond) != 1 or not isinstance(cond[key], list):
            raise OntologyError(f"{where}: '{key}' must be the only key and hold a list")
        for sub in cond[key]:
            _check_condition(sub, r, fact_sources, where, negated)
        return
    if "not" in cond:
        if len(cond) != 1:
            raise OntologyError(f"{where}: 'not' must be the only key")
        _check_condition(cond["not"], r, fact_sources, where, not negated)
        return
    if "fact" not in cond or "op" not in cond:
        raise OntologyError(f"{where}: leaf condition needs 'fact' and 'op'")
    if set(cond) - LEAF_KEYS:
        raise OntologyError(f"{where}: unknown leaf keys {sorted(set(cond) - LEAF_KEYS)}")
    missing = cond.get("missing", "error")
    if missing not in MISSING_POLICIES:
        raise OntologyError(f"{where}: 'missing' must be one of {sorted(MISSING_POLICIES)}")
    if missing == "false" and negated:
        raise OntologyError(f"{where}: 'missing: false' under 'not' would let absent evidence enable a rule")
    _check_fact_path(cond["fact"], fact_sources, where)
    op = cond["op"]
    if op in UNARY_OPS:
        if "value" in cond:
            raise OntologyError(f"{where}: operator {op!r} takes no value")
    elif op in COMPARISON_OPS:
        if "value" not in cond:
            raise OntologyError(f"{where}: operator {op!r} needs a value")
        _check_value_ref(cond["value"], r, fact_sources, where)
    else:
        raise OntologyError(f"{where}: unknown operator {op!r}")


def _check_fact_path(path: Any, fact_sources: tuple[str, ...], where: str) -> None:
    if not isinstance(path, str) or not path:
        raise OntologyError(f"{where}: fact path must be a non-empty string")
    parts = path.split(".")
    if UNTRUSTED_FIELDS & set(parts):
        raise OntologyError(f"{where}: case_note is untrusted free text and cannot be a rule input")
    if parts[0] not in fact_sources:
        raise OntologyError(f"{where}: fact {path!r} is not rooted in a grounded fact source {fact_sources}")


def _check_value_ref(value: Any, r: Rule, fact_sources: tuple[str, ...], where: str) -> None:
    if isinstance(value, str) and value.startswith("$"):
        _check_fact_path(value[1:], fact_sources, where)
    elif isinstance(value, str) and value.startswith("@"):
        if value[1:] not in r.params:
            raise OntologyError(f"{where}: unknown param reference {value!r}")


def _check_active_set(rules: tuple[Rule, ...]) -> None:
    fallbacks = [r for r in rules if r.is_fallback]
    if len(fallbacks) != 1:
        raise OntologyError(f"exactly one active fallback rule (when: {{all: []}}) is required, found {len(fallbacks)}")
    if rules[-1] is not fallbacks[0]:
        raise OntologyError(f"fallback rule {fallbacks[0].id} must have the lowest precedence (highest priority number)")
    priorities = [r.priority for r in rules]
    if len(set(priorities)) != len(priorities):
        raise OntologyError("active rules must have unique priorities so evaluation order is unambiguous")
    hard = [r.priority for r in rules if r.hard_stop]
    soft = [r for r in rules if not r.hard_stop]
    if hard and soft and min(r.priority for r in soft) < max(hard):
        shadowing = min(soft, key=lambda r: r.priority)
        raise OntologyError(f"rule {shadowing.id} would shadow a hard_stop rule; hard_stop rules must be evaluated first")


# --------------------------------------------------------------- evaluation
def _resolve_fact(path: str, facts: dict[str, Any]) -> Any:
    node: Any = facts
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise FactMissingError(path)
        node = node[part]
    return node


def _resolve_value(value: Any, facts: dict[str, Any], params: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        resolved = _resolve_fact(value[1:], facts)
        return list(resolved) if isinstance(resolved, list) else resolved
    if isinstance(value, str) and value.startswith("@"):
        return params[value[1:]]
    return value


def _holds(cond: dict[str, Any], facts: dict[str, Any], params: dict[str, Any]) -> bool:
    if "all" in cond:
        return all(_holds(c, facts, params) for c in cond["all"])
    if "any" in cond:
        return any(_holds(c, facts, params) for c in cond["any"])
    if "not" in cond:
        return not _holds(cond["not"], facts, params)
    try:
        return _holds_leaf(cond, facts, params)
    except FactMissingError:
        if cond.get("missing", "error") == "false":
            return False
        raise


def _holds_leaf(cond: dict[str, Any], facts: dict[str, Any], params: dict[str, Any]) -> bool:
    left = _resolve_fact(cond["fact"], facts)
    op = cond["op"]
    if op == "not_empty":
        return bool(left)
    if op == "empty":
        return not left
    if op == "is_true":
        return bool(left)
    if op == "is_false":
        return not left
    right = _resolve_value(cond["value"], facts, params)
    if left is None:
        raise FactMissingError(cond["fact"])
    try:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "in":
            return left in right
        if op == ">=":
            return left >= right
        if op == ">":
            return left > right
        if op == "<=":
            return left <= right
        if op == "<":
            return left < right
    except TypeError as exc:
        raise FactMissingError(f"{cond['fact']} is not comparable: {exc}") from exc
    raise OntologyError(f"unknown operator {op!r}")  # unreachable after validation


def _apply(r: Rule, facts: dict[str, Any]) -> RuleMatch:
    then = r.then
    return RuleMatch(
        rule=r,
        outcome=Outcome(then["outcome"]),
        action=then["action"],
        risk_level=_resolve_value(then["risk_level"], facts, r.params),
        reason_key=then["reason_key"],
        reason_params={k: _resolve_value(v, facts, r.params) for k, v in then["reason_params"].items()},
    )


# ------------------------------------------------------------------ loading
_active_override: Path | None = None
_active_lock = threading.Lock()


def activate(path: str | Path) -> Ontology:
    """Make a validated ontology file the process-wide active one (used by gated promotion)."""
    global _active_override
    onto = load_ontology(path)  # validate before switching
    with _active_lock:
        _active_override = Path(path)
    return onto


def reset_active() -> None:
    global _active_override
    with _active_lock:
        _active_override = None


def active_path() -> Path:
    return Path(_active_override or os.environ.get(ONTOLOGY_PATH_ENV) or DEFAULT_PATH)


def load_ontology(path: str | Path | None = None) -> Ontology:
    """Load and validate an ontology.

    Precedence: explicit `path` > a promoted ontology activated in-process >
    `KYC_ONTOLOGY_PATH` > the shipped `data/ontology.json`.
    """
    resolved = Path(path) if path else active_path()
    return _load_cached(str(resolved.resolve()), resolved.stat().st_mtime_ns)


@lru_cache(maxsize=8)
def _load_cached(path: str, _mtime: int) -> Ontology:
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        raise OntologyError(f"{path}: invalid JSON ({exc})") from exc
    return Ontology.from_dict(data)
