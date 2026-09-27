"""Authority matrix: who may approve what, and how many must agree.

The matrix lives in the ontology (`authority`), next to the rules it governs,
so a change in who-can-approve is versioned, validated, and replayable like
any other rule change. This module is pure: it computes the requirement for an
action at a risk level and checks each approval against it.

- `approvals`         how many approvals are needed;
- `roles`             which roles may approve;
- `distinct_approvers` maker-checker: the same person cannot approve twice;
- `distinct_roles`    each approval must come from a different role (e.g. KYC Lead + Compliance);
- `by_risk`           per-risk-level overrides (e.g. HIGH -> 2 approvals).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_APPROVER = "reviewer"
DEFAULT_ROLE = "analyst"


class AuthorityError(ValueError):
    """An approval that the authority matrix does not allow."""


@dataclass(frozen=True)
class Requirement:
    action: str
    approvals: int = 1
    roles: tuple[str, ...] = ("analyst", "kyc_lead", "compliance")
    distinct_approvers: bool = True
    distinct_roles: bool = False
    risk_level: str | None = None
    note: str = ""

    def to_payload(self, approvals_so_far: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        value = asdict(self)
        value["roles"] = list(self.roles)
        value["approvals_so_far"] = list(approvals_so_far or [])
        return value


def validate_matrix(authority: dict[str, Any], actions: tuple[str, ...]) -> None:
    """Loader-time checks: every write action has an entry; counts and roles are sane."""
    roles = set(authority.get("roles", []))
    entries = authority.get("actions", {})
    if not roles:
        raise ValueError("authority.roles must list at least one role")
    for action in (*actions, "promote_amendment"):
        if action not in entries:
            raise ValueError(f"authority has no entry for action {action!r}")
    for action, spec in entries.items():
        for tier in [spec, *spec.get("by_risk", {}).values()]:
            n = tier.get("approvals", spec.get("approvals", 1))
            allowed = set(tier.get("roles", spec.get("roles", [])))
            if not isinstance(n, int) or n < 1:
                raise ValueError(f"authority.{action}: approvals must be a positive integer")
            if not allowed or not allowed <= roles:
                raise ValueError(f"authority.{action}: roles must be a non-empty subset of {sorted(roles)}")
            if tier.get("distinct_roles", spec.get("distinct_roles")) and n > len(allowed):
                raise ValueError(f"authority.{action}: {n} distinct roles required but only {len(allowed)} allowed")


def requirement(authority: dict[str, Any] | None, action: str, risk_level: str | None = None) -> Requirement:
    spec = ((authority or {}).get("actions") or {}).get(action)
    if spec is None:
        return Requirement(action=action, risk_level=risk_level)
    tier = {**spec, **(spec.get("by_risk", {}).get(risk_level or "", {}))}
    return Requirement(
        action=action,
        approvals=int(tier.get("approvals", 1)),
        roles=tuple(tier.get("roles", Requirement.roles)),
        distinct_approvers=bool(tier.get("distinct_approvers", True)),
        distinct_roles=bool(tier.get("distinct_roles", False)),
        risk_level=risk_level,
        note=str(tier.get("note", "")),
    )


def normalize(response: dict[str, Any]) -> dict[str, str]:
    return {
        "approver": str(response.get("approver") or DEFAULT_APPROVER).strip(),
        "role": str(response.get("role") or DEFAULT_ROLE).strip(),
    }


def check_approval(req: dict[str, Any] | Requirement, approvals: list[dict[str, Any]], response: dict[str, Any]) -> dict[str, str]:
    """Validate one more approval against the requirement; return the normalized approval."""
    r = req if isinstance(req, dict) else req.to_payload()
    approval = normalize(response)
    if not approval["approver"]:
        raise AuthorityError("approver is required")
    if approval["role"] not in r.get("roles", []):
        raise AuthorityError(f"role {approval['role']!r} cannot approve {r.get('action')}; allowed: {r.get('roles')}")
    if r.get("distinct_approvers", True) and approval["approver"] in {a["approver"] for a in approvals}:
        raise AuthorityError(f"maker-checker: {approval['approver']} has already approved; a different approver is required")
    if r.get("distinct_roles") and approval["role"] in {a["role"] for a in approvals}:
        raise AuthorityError(f"an approval from role {approval['role']!r} is already recorded; a different role is required")
    return approval


def satisfied(req: dict[str, Any] | Requirement, approvals: list[dict[str, Any]]) -> bool:
    r = req if isinstance(req, dict) else req.to_payload()
    return len(approvals) >= int(r.get("approvals", 1))
