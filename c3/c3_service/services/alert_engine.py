"""AlertEngine: turns a classifier result into at most one alert (module M1 of Part III;
BR-C3-01 to BR-C3-06 and BR-C3-12; Section 4.1, Table 102; pseudocode of Section 3.2.1).

    handle()        the result call: 404 checks, a stored decision wins (BR-C3-12), error -> ERROR_RECORDED,
                    else decide() and create_alert() or store the no-alert decision
    decide()        reads only: for each of the top 3 classes, highest confidence first (BR-C3-01), the rule
                    (BR-C3-02, 03), then judge_class() in the order of Table 102; pick_winner() (BR-C3-04)
    create_alert()  ONE transaction: INSERT alert + INSERT alert_action(CREATE); the unique key
                    (event_ref, alert_type_code) turns a racing duplicate into DUPLICATE_RESULT.
                    After the COMMIT only: audio_events.decision, the notifications (after_alert_created:
                    the rows now, the emails in the background), alert.created.

judge_class() and pick_winner() are pure, so every row of the decision tables is a unit test.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..auth import TenantDirectory
from ..errors import NotFound
from ..models import CLASS_IDS, Alert, AlertAction
from ..timeutil import utcnow
from .alert_query import alert_row
from .event_store import ResultEvents, location_of
from .publisher import Publisher, alert_created_payload, safe_publish
from .rule_resolver import SEVERITY_RANK, RuleResolver, RuleView, select_rule, severity_of, state_of

log = logging.getLogger("scicp.c3.engine")


@dataclass(frozen=True)
class Candidate:
    rule: RuleView
    class_name: str
    conf: float
    severity: str


# -- Table 102, one class (R2 to R5; R1 is select_rule's NO_RULE / STATE_MISMATCH) -------------------
async def judge_class(conf: float, rule: RuleView, service_on: Callable[[], Awaitable[bool]],
                      repeats: Callable[[], Awaitable[int]]) -> Optional[str]:
    """None when the class is a candidate (R5), else the reason. The checks run in the table's order and the
    later ones (a database read, a MongoDB count) only when the earlier ones passed."""
    if conf < rule.min_confidence:
        return "BELOW_THRESHOLD"                                  # R2
    if not await service_on():
        return "SERVICE_OFF"                                      # R3
    if rule.min_repeats > 1 and await repeats() < rule.min_repeats:
        return "REPEAT_PENDING"                                   # R4
    return None                                                   # R5


# -- BR-C3-04 -------------------------------------------------------------------------------------
def pick_winner(candidates: Sequence[Candidate]) -> Candidate:
    """One alert per event: the highest severity wins, ties go to the higher confidence."""
    return max(candidates, key=lambda c: (SEVERITY_RANK.get(c.severity, 0), c.conf))


def duplicate_of(stored: Dict[str, Any]) -> Dict[str, Any]:
    """BR-C3-12: a repeated result answers the first decision, with reason DUPLICATE_RESULT."""
    return {"alert": bool(stored.get("alert")), "alertId": stored.get("alertId"), "alertType": stored.get("alertType"),
            "severity": stored.get("severity"), "reason": "DUPLICATE_RESULT"}


class AlertEngine:
    def __init__(self, session_factory: Any, events: ResultEvents, publisher: Publisher, tenants: TenantDirectory,
                 notifier: Any = None) -> None:
        self.session_factory = session_factory
        self.events = events
        self.publisher = publisher
        self.tenants = tenants
        self.notifier = notifier  # NotificationService; None writes no notifications (the seed-consistency test)

    async def after_alert_created(self, alert: Alert) -> None:
        """The notification hook (BR-C3-11), after the COMMIT: never fails the result call."""
        if self.notifier is None:
            return
        try:
            await self.notifier.after_alert_created(alert)
        except Exception as exc:  # noqa: BLE001 - NotificationService already logs; this guards a replacement
            log.error("alert %s: notification hook failed: %s", alert.alert_id, exc)

    # -- the result call ----------------------------------------------------------------------------
    async def handle(self, event_ref: str, tenant_code: str, vehicle_id: str, top_k: List[Tuple[str, float]],
                     model_version: str, error: Optional[str]) -> Dict[str, Any]:
        tenant_id = await self.tenants.id_of(tenant_code)
        event = await self.events.get_for_vehicle(event_ref, tenant_code, vehicle_id) if tenant_id else None
        if event is None:
            raise NotFound("Event not found for this tenant and vehicle")
        if event.get("decision"):
            return duplicate_of(event["decision"])              # redelivery: the first decision wins
        if error:
            return await self._no_alert(event, "ERROR_RECORDED")  # BR-C3-13: stored by the worker, no alert
        decision, best, state = await self.decide(event, tenant_id, top_k)
        if best is None:
            return await self._no_alert(event, decision["reason"])
        return await self.create_alert(event, tenant_id, best, state, model_version)

    # -- the decision (reads only) --------------------------------------------------------------------
    async def decide(self, event: Dict[str, Any], tenant_id: int, top_k: List[Tuple[str, float]]
                     ) -> Tuple[Dict[str, Any], Optional[Candidate], str]:
        """(decision, winning candidate or None, vehicle state). Writes nothing."""
        state = state_of(event.get("context"))
        candidates: List[Candidate] = []
        first_reason: Optional[str] = None
        async with self.session_factory() as session:
            resolver = RuleResolver(session, self.events)
            rules = await resolver.rules_for(tenant_id)
            default_severity = await resolver.default_severities()
            for class_name, conf in sorted(top_k, key=lambda s: s[1], reverse=True)[:3]:   # BR-C3-01
                rule, why_not = select_rule(rules, tenant_id, class_name, state)            # BR-C3-02, BR-C3-03
                if rule is None:
                    first_reason = first_reason or why_not                                  # R1
                    continue
                reason = await judge_class(
                    conf, rule,
                    service_on=lambda r=rule: resolver.service_on(tenant_id, event["vehicleId"], r.alert_type),
                    repeats=lambda r=rule: resolver.repeats(event, r))
                if reason is not None:
                    first_reason = first_reason or reason
                    continue
                candidates.append(Candidate(rule, class_name, conf,
                                            severity_of(rule, default_severity.get(rule.alert_type, "LOW"))))  # BR-C3-06
        if not candidates:
            return {"alert": False, "reason": first_reason or "NO_RULE"}, None, state
        best = pick_winner(candidates)
        return ({"alert": True, "alertType": best.rule.alert_type, "severity": best.severity,
                 "reason": "ALERT_CREATED"}, best, state)

    # -- the writes ---------------------------------------------------------------------------------------
    async def _no_alert(self, event: Dict[str, Any], reason: str) -> Dict[str, Any]:
        decision = {"alert": False, "reason": reason}
        await self.events.set_decision_if_absent(event, decision)
        return decision

    async def create_alert(self, event: Dict[str, Any], tenant_id: int, best: Candidate, state: str,
                           model_version: str) -> Dict[str, Any]:
        now = utcnow()
        now = now.replace(microsecond=now.microsecond // 1000 * 1000)  # DATETIME(3)
        position = location_of(event)
        event_ref = str(event["_id"])
        async with self.session_factory() as session:
            row = Alert(tenant_id=tenant_id, vehicle_id=event["vehicleId"], alert_type_code=best.rule.alert_type,
                        rule_id=best.rule.rule_id, top_class_id=CLASS_IDS[best.class_name], severity=best.severity,
                        status="NEW", event_ref=event_ref, confidence=round(best.conf, 3), vehicle_state=state,
                        model_version=model_version, count=1,
                        lat=position[0] if position else None, lon=position[1] if position else None,
                        created_at=now, updated_at=now)
            try:
                session.add(row)
                await session.flush()   # the INSERT: a second alert of this type for this event fails here
                session.add(AlertAction(alert_id=row.alert_id, user_id=None, action="CREATE", from_status=None,
                                        to_status="NEW", ts=now))
                await session.commit()
            except IntegrityError:
                await session.rollback()
                stored = (await session.execute(select(Alert).where(
                    Alert.event_ref == event_ref, Alert.alert_type_code == best.rule.alert_type))).scalar_one()
                log.info("event %s: result repeated while the first was being stored -> alert %s", event_ref,
                         stored.alert_id)
                return {"alert": True, "alertId": stored.alert_id, "alertType": stored.alert_type_code,
                        "severity": stored.severity, "reason": "DUPLICATE_RESULT"}
        # --- committed: everything below is best effort ---
        decision = {"alert": True, "alertId": row.alert_id, "alertType": row.alert_type_code, "severity": row.severity,
                    "reason": "ALERT_CREATED"}
        await self.events.set_decision_if_absent(event, decision)
        await self.after_alert_created(row)
        await safe_publish(self.publisher, "alert.created", alert_created_payload(alert_row(row, {}, now)), tenant_id)
        log.info("event %s: %s %s alert %s on %s (%s %.2f, rule %s)", event_ref, row.severity, row.alert_type_code,
                 row.alert_id, row.vehicle_id, best.class_name, best.conf, best.rule.rule_id)
        return decision
