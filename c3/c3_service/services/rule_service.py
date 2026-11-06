"""RuleService: the alert rules a tenant sees and the changes it may make (module M6 of
Part III; Section 2.2, Table 84; BR-C3-09 and BR-C3-10).

    effective_list(tenant)   the platform defaults, each replaced in place by the tenant's override
                             of the same class, state and alert type (the view the engine uses, BR-C3-03)
    save(caller, id, changes)  PUT: on a default with a tenant scope -> that tenant's override (201,
                             or 200 when it exists); on an override in scope -> 200; a default itself only
                             by SYS_ADMIN without tenantId
    delete(caller, id)       DELETE: an override in scope -> 204 (the default applies again); a default -> 409

check_values (BR-C3-09) and check_permission / check_safety (BR-C3-10) are pure functions,
one unit test each. Every save and delete writes one row in C1's audit_log in the same
transaction (RULE_SAVE / RULE_DELETE), the row C1's AuditLogger.write would build.
POST /alert-rules (a new rule) is design only this semester.
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from ..auth import Caller
from ..errors import BusinessRule, Conflict, Forbidden, NotFound, Validation
from ..models import AlertRule, AlertType, AppUser, AuditLog
from ..timeutil import iso, utcnow
from .rule_resolver import SEVERITY_RANK, _view, severity_of

NEVER_WEAKER = ("EMERGENCY", "ACCIDENT")  # BR-C3-10: not disabled by a tenant, never below HIGH
LIMITS = {"minConfidence": (0.40, 0.99), "minRepeats": (1, 5), "windowSeconds": (5, 60)}  # BR-C3-09
COLUMN = {"minConfidence": "min_confidence", "severityOverride": "severity_override", "minRepeats": "min_repeats",
          "windowSeconds": "window_s", "enabled": "enabled"}


# -- BR-C3-09 / BR-C3-10, pure ---------------------------------------------------------------------
def check_values(min_confidence: float, min_repeats: int, window_s: Optional[int]) -> None:
    """BR-C3-09 on the rule as it would be saved: 422 BUSINESS_RULE with the field and its bounds."""
    for field, value in (("minConfidence", min_confidence), ("minRepeats", min_repeats), ("windowSeconds", window_s)):
        low, high = LIMITS[field]
        if value is not None and not low <= value <= high:
            raise BusinessRule("BR-C3-09", f"{field} must be between {low} and {high}", field=field, min=low, max=high)
    if min_repeats > 1 and window_s is None:
        raise BusinessRule("BR-C3-09", "windowSeconds is required when minRepeats is over 1", field="windowSeconds",
                           min=LIMITS["windowSeconds"][0], max=LIMITS["windowSeconds"][1])


def check_safety(alert_type: str, is_override: bool, enabled: bool, severity_override: Optional[str]) -> None:
    """BR-C3-10: an EMERGENCY or ACCIDENT rule is never below HIGH (any rule; Table 103 S4) and a tenant
    override may not disable it (the owner switches the service off in the subscription instead)."""
    if alert_type not in NEVER_WEAKER:
        return
    if severity_override is not None and SEVERITY_RANK[severity_override] < SEVERITY_RANK["HIGH"]:
        raise BusinessRule("BR-C3-10", f"{alert_type} rules are never below HIGH", field="severityOverride")
    if is_override and not enabled:
        raise BusinessRule("BR-C3-10", f"A tenant may not disable an {alert_type} rule; switch the service off "
                           "in the subscription instead", field="enabled")


def check_permission(caller: Caller, rule_tenant: Optional[int]) -> Optional[int]:
    """BR-C3-10, who edits what. Returns the tenant the change is for (None = the default itself).
    FLEET_MANAGER: its own tenant only. SERVICE_STAFF: any tenant, tenantId required. SYS_ADMIN: any tenant,
    and the defaults without tenantId. Service tokens and the other roles: 403. Another tenant's override: 404."""
    if caller.is_service or caller.role not in ("FLEET_MANAGER", "SERVICE_STAFF", "SYS_ADMIN"):
        raise Forbidden("Not allowed to change alert rules", {"rule": "BR-C3-10"})
    if caller.tenant_id is None:
        if caller.role != "SYS_ADMIN":
            raise Validation("tenantId is required", {"field": "tenantId"})
        return rule_tenant                      # SYS_ADMIN: a default, or an override of any tenant
    if rule_tenant is not None and rule_tenant != caller.tenant_id:
        raise NotFound("Rule not found")       # another tenant's override does not exist for this caller
    return caller.tenant_id


# -- helpers ----------------------------------------------------------------------------------------------
def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def snapshot(rule: AlertRule) -> Dict[str, Any]:
    """The rule's columns, JSON-safe, as C1's audit snapshot()."""
    return {c.key: _jsonable(getattr(rule, c.key)) for c in AlertRule.__table__.columns}


class RuleService:
    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory

    # -- GET -------------------------------------------------------------------------------------------
    async def effective_list(self, tenant_id: Optional[int]) -> List[Dict[str, Any]]:
        async with self.session_factory() as session:
            return await self._effective(session, tenant_id)

    async def _effective(self, session, tenant_id: Optional[int]) -> List[Dict[str, Any]]:
        rows = (await session.execute(select(AlertRule).where(
            or_(AlertRule.tenant_id.is_(None), AlertRule.tenant_id == tenant_id) if tenant_id is not None
            else AlertRule.tenant_id.is_(None)).order_by(AlertRule.rule_id))).scalars().all()
        defaults = [r for r in rows if r.tenant_id is None]
        overrides = {self._key(r): r for r in rows if r.tenant_id is not None}
        chosen = [overrides.pop(self._key(d), d) for d in defaults] + list(overrides.values())
        severities = dict((await session.execute(select(AlertType.code, AlertType.default_severity))).all())
        names = await self._names(session, [r.updated_by for r in chosen])
        return [self.item(r, severities, names) for r in chosen]

    @staticmethod
    def _key(rule: AlertRule) -> Tuple[int, str, str]:
        return rule.class_id, rule.vehicle_state, rule.alert_type_code

    @staticmethod
    async def _names(session, user_ids) -> Dict[int, str]:
        ids = sorted({u for u in user_ids if u is not None})
        if not ids:
            return {}
        return dict((await session.execute(select(AppUser.user_id, AppUser.name).where(AppUser.user_id.in_(ids)))).all())

    @staticmethod
    def item(rule: AlertRule, severities: Dict[str, str], names: Dict[int, str]) -> Dict[str, Any]:
        view = _view(rule)
        return {"ruleId": rule.rule_id, "scope": "DEFAULT" if rule.tenant_id is None else "TENANT",
                "tenantId": rule.tenant_id, "alertType": rule.alert_type_code, "class": view.class_name,
                "vehicleState": rule.vehicle_state, "minConfidence": view.min_confidence,
                "severity": severity_of(view, severities.get(rule.alert_type_code, "LOW")),
                "severityOverride": rule.severity_override, "minRepeats": view.min_repeats,
                "windowSeconds": rule.window_s, "enabled": view.enabled, "soundClass": view.class_name,
                "updatedBy": names.get(rule.updated_by) if rule.updated_by else None, "updatedAt": iso(rule.updated_at)}

    # -- PUT -------------------------------------------------------------------------------------------
    async def save(self, caller: Caller, rule_id: int, changes: Dict[str, Any],
                   ip: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
        """(the saved rule, created?). 201 when it created the tenant's override, else 200."""
        now = utcnow()
        async with self.session_factory() as session:
            rule = await session.get(AlertRule, rule_id)
            if rule is None:
                raise NotFound("Rule not found")
            for_tenant = check_permission(caller, rule.tenant_id)
            target, created = rule, False
            if rule.tenant_id is None and for_tenant is not None:     # a default seen by a tenant: its override
                target = (await session.execute(select(AlertRule).where(
                    AlertRule.tenant_id == for_tenant, AlertRule.class_id == rule.class_id,
                    AlertRule.vehicle_state == rule.vehicle_state,
                    AlertRule.alert_type_code == rule.alert_type_code))).scalars().first()
                if target is None:
                    target, created = AlertRule(tenant_id=for_tenant, alert_type_code=rule.alert_type_code,
                                                class_id=rule.class_id, vehicle_state=rule.vehicle_state,
                                                min_confidence=rule.min_confidence,
                                                severity_override=rule.severity_override,
                                                min_repeats=rule.min_repeats, window_s=rule.window_s,
                                                enabled=rule.enabled), True
            before = None if created else snapshot(target)
            values = {"min_confidence": float(target.min_confidence), "severity_override": target.severity_override,
                      "min_repeats": int(target.min_repeats), "window_s": target.window_s, "enabled": bool(target.enabled)}
            values.update({COLUMN[k]: v for k, v in changes.items()})
            nulled = [k for k in ("minConfidence", "minRepeats", "enabled") if k in changes and changes[k] is None]
            if nulled:
                raise Validation("minConfidence, minRepeats and enabled cannot be null", {"field": nulled[0]})
            check_values(values["min_confidence"], values["min_repeats"], values["window_s"])
            check_safety(target.alert_type_code, target.tenant_id is not None, values["enabled"],
                         values["severity_override"])
            values["min_confidence"] = round(values["min_confidence"], 2)     # DECIMAL(3,2)
            for column, value in values.items():
                setattr(target, column, value)
            target.updated_by, target.updated_at = caller.user_id, now
            if created:
                session.add(target)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                raise Conflict("The override was created by another request; retry", {"ruleId": rule_id})
            await session.refresh(target)  # tenant_key is generated by the database: load it for the audit snapshot
            session.add(AuditLog(actor_user_id=caller.user_id, tenant_id=target.tenant_id, action="RULE_SAVE",
                                 entity="alert_rule", entity_id=str(target.rule_id), before_json=before,
                                 after_json=snapshot(target), ip=ip, ts=now))
            await session.commit()
            severities = dict((await session.execute(select(AlertType.code, AlertType.default_severity))).all())
            names = await self._names(session, [target.updated_by])
            return self.item(target, severities, names), created

    # -- DELETE ----------------------------------------------------------------------------------------
    async def delete(self, caller: Caller, rule_id: int, ip: Optional[str] = None) -> None:
        async with self.session_factory() as session:
            rule = await session.get(AlertRule, rule_id)
            if rule is None:
                raise NotFound("Rule not found")
            if caller.is_service or caller.role not in ("FLEET_MANAGER", "SERVICE_STAFF", "SYS_ADMIN"):
                raise Forbidden("Not allowed to change alert rules", {"rule": "BR-C3-10"})
            if rule.tenant_id is None:
                raise Conflict("A default rule cannot be deleted; change it with PUT", {"ruleId": rule_id})
            check_permission(caller, rule.tenant_id)
            session.add(AuditLog(actor_user_id=caller.user_id, tenant_id=rule.tenant_id, action="RULE_DELETE",
                                 entity="alert_rule", entity_id=str(rule.rule_id), before_json=snapshot(rule),
                                 after_json=None, ip=ip, ts=utcnow()))
            await session.delete(rule)
            await session.commit()
