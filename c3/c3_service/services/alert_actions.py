"""AlertActionService: Acknowledge, Resolve and False alarm on one alert (module M2 of
Part III; the action row of Table 83; sequence diagram SQ2 of Section 3.4.2).

One call does, in this order:
  1. refuse RIDER and IOT_ENGINEER (403; Table 89), find the alert in the caller's scope (404);
  2. ask AlertStateMachine whether the action is allowed from the current status (409);
  3. in ONE transaction: the guarded UPDATE
         UPDATE alert SET status=:new, ... WHERE alert_id=:id AND status=:expected
     and the INSERT into alert_action. If the UPDATE changed no row, someone else changed
     the alert since step 2: roll back and answer 409, so two users pressing Acknowledge
     at the same time give one transition and one 409 (Section 2.7, Idempotency);
  4. only after the COMMIT: publish alert.updated, then post the feedback label to C1.
     Both are best effort: a failure is logged and the answer is still 200.

The row changes are the stub's: ACKNOWLEDGE sets acknowledged_by/at; RESOLVE and
FALSE_ALARM set closed_by/at, and also acknowledged_by/at when the alert was never
acknowledged (closed straight from NEW), so C1's mean acknowledge time counts it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy import select, update

from ..auth import Caller, forbid_roles
from ..errors import Conflict, NotFound
from ..models import Alert, AlertAction, AppUser
from ..timeutil import utcnow
from .alert_query import alert_row, scope
from .alert_state import FINAL_STATUSES, AlertStateMachine
from .c1_client import C1Client
from .publisher import Publisher, safe_publish

log = logging.getLogger("scicp.c3.actions")

FEEDBACK_LABEL = {"RESOLVE": "CONFIRMED", "FALSE_ALARM": "FALSE_ALARM"}  # ACKNOWLEDGE posts none


class AlertActionService:
    def __init__(self, session_factory: Any, publisher: Publisher, c1: C1Client) -> None:
        self.session_factory = session_factory
        self.publisher = publisher
        self.c1 = c1

    async def act(self, caller: Caller, alert_id: int, action: str, note: Optional[str]) -> Dict[str, Any]:
        forbid_roles(caller, "RIDER", "IOT_ENGINEER")  # riders read only (BR-C3-08)
        now = utcnow()
        now = now.replace(microsecond=now.microsecond // 1000 * 1000)  # what DATETIME(3) keeps, answer == row
        async with self.session_factory() as session:
            row = (await session.execute(scope(select(Alert).where(Alert.alert_id == alert_id), caller))).scalars().first()
            if row is None:
                raise NotFound("Alert not found")
            from_status = row.status
            to_status = AlertStateMachine.check(from_status, action)
            await self._transition(session, row, caller, action, from_status, to_status, note, now)
            row = (await session.execute(select(Alert).where(Alert.alert_id == alert_id)
                                         .execution_options(populate_existing=True))).scalar_one()
            names = await self._names(session, [row.acknowledged_by, row.closed_by, caller.user_id])
        # --- committed: everything below is best effort and cannot change the answer ---
        by = names.get(caller.user_id) if caller.user_id is not None else caller.name
        dto = alert_row(row, names, now)
        await safe_publish(self.publisher, "alert.updated",
                           {"alertId": alert_id, "status": to_status, "by": by, "action": action,
                            "vehicleId": row.vehicle_id}, row.tenant_id)
        await self._feedback(caller, action, row.event_ref)
        return dto

    async def _transition(self, session, row: Alert, caller: Caller, action: str, from_status: str, to_status: str,
                          note: Optional[str], now) -> None:
        """The guarded UPDATE and the alert_action INSERT, committed together; 409 if the status moved meanwhile."""
        alert_id = row.alert_id  # read now: a rollback expires `row`, and reloading it lazily is not allowed in async
        values: Dict[str, Any] = {"status": to_status, "updated_at": now}
        if action == "ACKNOWLEDGE" or row.acknowledged_at is None:
            values.update(acknowledged_by=caller.user_id, acknowledged_at=now)
        if to_status in FINAL_STATUSES:
            values.update(closed_by=caller.user_id, closed_at=now)
        result = await session.execute(
            update(Alert).where(Alert.alert_id == alert_id, Alert.status == from_status).values(**values)
            .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            await session.rollback()
            current = (await session.execute(select(Alert.status).where(Alert.alert_id == alert_id))).scalar_one()
            log.info("alert %s: %s lost the race (%s -> %s, now %s)", alert_id, action, from_status, to_status, current)
            AlertStateMachine.check(current, action)  # raises the 409 with the status as it is now
            raise Conflict("Action not allowed in this state",  # only if the status went back, which no action does
                           {"rule": "BR-C3-07", "from": current, "to": to_status,
                            "allowed": AlertStateMachine.allowed_actions(current)})
        session.add(AlertAction(alert_id=alert_id, user_id=caller.user_id, action=action, from_status=from_status,
                                to_status=to_status, note=note, ts=now))
        await session.commit()

    async def _feedback(self, caller: Caller, action: str, event_ref: Optional[str]) -> None:
        """CONFIRMED on RESOLVE, FALSE_ALARM on FALSE_ALARM, with the user's token; none for a service token."""
        label = FEEDBACK_LABEL.get(action)
        if label is None or caller.is_service or not event_ref:
            return
        try:
            await self.c1.post_feedback(event_ref, label, caller.token)
        except Exception as exc:  # noqa: BLE001 - C1Client already never raises; this guards a replacement
            log.info("feedback %s on %s failed: %s", label, event_ref, exc)

    @staticmethod
    async def _names(session, user_ids) -> Dict[int, str]:
        ids = sorted({int(u) for u in user_ids if u is not None})
        if not ids:
            return {}
        rows = (await session.execute(select(AppUser.user_id, AppUser.name).where(AppUser.user_id.in_(ids)))).all()
        return {int(uid): name for uid, name in rows}
