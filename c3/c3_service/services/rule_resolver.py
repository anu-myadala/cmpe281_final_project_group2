"""RuleResolver: which rule applies to a class of a result, in which vehicle state, with
which severity, and whether its repeats are met (Part III, Table 93; BR-C3-02, 03, 05, 06).

The decisions are pure functions over plain values (state_of, select_rule, severity_of),
so each business rule has one place in the code and its own unit test. The class
RuleResolver is the database half: it loads the rules of a tenant, checks the
subscription, and counts the repeats in MongoDB.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import or_, select

from ..models import AlertRule, AlertType, Subscription, Vehicle, sound_class_name
from .event_store import ResultEvents

MOVING_ABOVE_KPH = 5.0
SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
NEVER_BELOW_HIGH = ("EMERGENCY", "ACCIDENT")


@dataclass(frozen=True)
class RuleView:
    """One alert_rule row, as the engine needs it."""
    rule_id: int
    tenant_id: Optional[int]
    alert_type: str
    class_name: str
    vehicle_state: str           # ANY | MOVING | PARKED
    min_confidence: float
    severity_override: Optional[str]
    min_repeats: int
    window_s: Optional[int]
    enabled: bool


# -- BR-C3-02 -------------------------------------------------------------------------------------
def state_of(context: Optional[Dict[str, Any]]) -> str:
    """MOVING when the ignition is on and speedKph is over 5; otherwise PARKED (missing values count as off / 0)."""
    context = context or {}
    speed = context.get("speedKph")
    moving = bool(context.get("ignition")) and isinstance(speed, (int, float)) and speed > MOVING_ABOVE_KPH
    return "MOVING" if moving else "PARKED"


# -- BR-C3-03 -------------------------------------------------------------------------------------
def effective_rules(rules: Iterable[RuleView], tenant_id: int) -> List[RuleView]:
    """The tenant's view of the rules: a tenant override replaces the default with the same class, state and
    alert type; other tenants' rows are ignored. Disabled rules stay in the list (select_rule skips them)."""
    by_key: Dict[Tuple[str, str, str], RuleView] = {}
    for rule in sorted(rules, key=lambda r: (r.tenant_id is not None, r.rule_id)):  # defaults first, then overrides
        if rule.tenant_id not in (None, tenant_id):
            continue
        by_key[(rule.class_name, rule.vehicle_state, rule.alert_type)] = rule
    return sorted(by_key.values(), key=lambda r: r.rule_id)


def select_rule(rules: Iterable[RuleView], tenant_id: int, class_name: str, state: str
                ) -> Tuple[Optional[RuleView], Optional[str]]:
    """(rule, None) when an enabled rule for the class matches the state (a state-specific rule before ANY);
    else (None, "STATE_MISMATCH") when the class has an enabled rule only for the other state, or (None, "NO_RULE")."""
    enabled = [r for r in effective_rules(rules, tenant_id) if r.class_name == class_name and r.enabled]
    for wanted in (state, "ANY"):
        for rule in enabled:
            if rule.vehicle_state == wanted:
                return rule, None
    return None, ("STATE_MISMATCH" if enabled else "NO_RULE")


# -- BR-C3-06 -------------------------------------------------------------------------------------
def severity_of(rule: RuleView, default_severity: str) -> str:
    """The rule's override, else the alert type's default; EMERGENCY and ACCIDENT are never below HIGH."""
    severity = rule.severity_override or default_severity
    if rule.alert_type in NEVER_BELOW_HIGH and SEVERITY_RANK.get(severity, 0) < SEVERITY_RANK["HIGH"]:
        return "HIGH"
    return severity


# -- the database half ----------------------------------------------------------------------------------
def _view(row: AlertRule) -> RuleView:
    return RuleView(rule_id=row.rule_id, tenant_id=row.tenant_id, alert_type=row.alert_type_code,
                    class_name=sound_class_name(row.class_id), vehicle_state=row.vehicle_state,
                    min_confidence=float(row.min_confidence), severity_override=row.severity_override,
                    min_repeats=int(row.min_repeats), window_s=row.window_s, enabled=bool(row.enabled))


class RuleResolver:
    def __init__(self, session, events: ResultEvents) -> None:
        self.session = session
        self.events = events

    async def rules_for(self, tenant_id: int) -> List[RuleView]:
        """The platform defaults and this tenant's overrides."""
        rows = (await self.session.execute(select(AlertRule).where(
            or_(AlertRule.tenant_id.is_(None), AlertRule.tenant_id == tenant_id)))).scalars().all()
        return [_view(r) for r in rows]

    async def default_severities(self) -> Dict[str, str]:
        rows = (await self.session.execute(select(AlertType.code, AlertType.default_severity))).all()
        return {code: severity for code, severity in rows}

    async def service_on(self, tenant_id: int, vehicle_code: str, alert_type: str) -> bool:
        """BR-05 of C1: an enabled subscription row for this vehicle and this service (= alert type). The row is
        keyed by the numeric vehicle_id, found through the vehicle table by the public code."""
        enabled = (await self.session.execute(
            select(Subscription.enabled).join(Vehicle, Vehicle.vehicle_id == Subscription.vehicle_id)
            .where(Vehicle.tenant_id == tenant_id, Vehicle.vehicle_code == vehicle_code,
                   Subscription.service_code == alert_type))).scalar_one_or_none()
        return bool(enabled)

    async def repeats(self, event: Dict[str, Any], rule: RuleView) -> int:
        """BR-C3-05: events of the same top class for the same vehicle within window_s, this one included."""
        if rule.min_repeats <= 1:
            return 1
        return await self.events.count_same_top_class(event, rule.class_name, rule.window_s or 0)
