"""NotificationService: the notification rows of a new alert, and the emails sent after the
response (module M5 of Part III; BR-C3-11; the notification state diagram, Table 99).

    after_alert_created(alert)   the AlertEngine hook, called after the alert's COMMIT:
                                 RecipientResolver (Table 104) -> enqueue() -> schedule()
    enqueue()                    one transaction of its own: the DASHBOARD row SENT at once,
                                 one EMAIL row per recipient PENDING with attempts 0
    send_pending(alert_id)       each PENDING email: up to 3 attempts after the waits of
                                 NOTIFY_RETRY_WAITS (0, 30, 120 s); SENT, or FAILED with last_error

Why asyncio.create_task and not FastAPI BackgroundTasks: BackgroundTasks run inside the same
request cycle, and the hook lives in the service layer, not in the route. With waits of 30 and
120 s an in-process client (the tests, C1's TestClient) would wait for them. A task started
here returns the response at once; the service keeps a reference to every task (so none is
garbage-collected), drain() waits for them (tests), and close() cancels them at shutdown.
Nothing here ever changes the alert, and every failure is logged, never raised.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, List, Optional, Sequence, Set

from sqlalchemy import select, update

from ..models import Alert, AppUser, Notification, Tenant
from ..timeutil import utcnow
from .alert_mailer import AlertMailer, describe
from .recipient_resolver import Recipient, RecipientResolver

log = logging.getLogger("scicp.c3.notify")


class NotificationService:
    def __init__(self, session_factory: Any, mailer: AlertMailer, retry_waits: Sequence[float] = (0, 30, 120),
                 clock: Callable[[], datetime] = utcnow) -> None:
        self.session_factory = session_factory
        self.mailer = mailer
        self.retry_waits = list(retry_waits) or [0]
        self.clock = clock  # "now" for the quiet hours and the rider window; the tests set it
        self._tasks: Set[asyncio.Task] = set()

    # -- the hook ---------------------------------------------------------------------------------------
    async def after_alert_created(self, alert: Alert) -> None:
        """Rows now, emails in the background. Never raises: the alert is already committed and answered."""
        try:
            now = self.clock()
            async with self.session_factory() as session:
                recipients, _ = await RecipientResolver(session).for_alert(alert, now)
            await self.enqueue(alert, recipients, now)
        except Exception as exc:  # noqa: BLE001
            log.error("alert %s: notifications not recorded: %s", alert.alert_id, exc)
            return
        if any(r.channel == "EMAIL" for r in recipients):
            self.schedule(alert.alert_id)

    async def enqueue(self, alert: Alert, recipients: List[Recipient], now: Optional[datetime] = None) -> List[int]:
        """INSERT the rows in one transaction of their own; returns their ids."""
        now = now or utcnow()
        rows = [Notification(alert_id=alert.alert_id, tenant_id=alert.tenant_id, recipient_user_id=r.user_id,
                             channel=r.channel, status="SENT" if r.channel == "DASHBOARD" else "PENDING",
                             attempts=0, created_at=now, sent_at=now if r.channel == "DASHBOARD" else None)
                for r in recipients]
        async with self.session_factory() as session:
            session.add_all(rows)
            await session.commit()
        log.info("alert %s: %d notification rows (%s)", alert.alert_id, len(rows),
                 ", ".join(f"{r.channel}:{r.email or 'room'}" for r in recipients))
        return [r.notification_id for r in rows]

    # -- the background sending -----------------------------------------------------------------------
    def schedule(self, alert_id: int) -> asyncio.Task:
        task = asyncio.create_task(self.send_pending(alert_id), name=f"notify-alert-{alert_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        """Waits for every email task started so far (tests; a graceful stop)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        """Shutdown: stop the waits. Emails still PENDING stay PENDING in the table."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def send_pending(self, alert_id: int) -> None:
        try:
            async with self.session_factory() as session:
                alert = await session.get(Alert, alert_id)
                time_zone = (await session.execute(select(Tenant.time_zone).where(
                    Tenant.tenant_id == alert.tenant_id))).scalar_one_or_none() or "UTC"
                pending = (await session.execute(
                    select(Notification.notification_id, AppUser.email)
                    .join(AppUser, AppUser.user_id == Notification.recipient_user_id)
                    .where(Notification.alert_id == alert_id, Notification.channel == "EMAIL",
                           Notification.status == "PENDING").order_by(Notification.notification_id))).all()
            for notification_id, email in pending:
                await self._deliver(notification_id, alert, email, time_zone)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a background task has nobody to raise to
            log.error("alert %s: sending the notifications failed: %s", alert_id, exc)

    async def _deliver(self, notification_id: int, alert: Alert, email: str, time_zone: str) -> None:
        """BR-C3-11: attempts after waits of 0, 30 and 120 s; each attempt is recorded."""
        last_error = None
        for attempt, wait_s in enumerate(self.retry_waits, start=1):
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            try:
                await self.mailer.send(alert, email, time_zone)
            except Exception as exc:  # noqa: BLE001 - any failure is one failed attempt
                last_error = describe(exc)
                log.info("alert %s: email to %s, attempt %d failed: %s", alert.alert_id, email, attempt, last_error)
                await self._mark(notification_id, attempts=attempt, last_error=last_error)
                continue
            await self._mark(notification_id, attempts=attempt, status="SENT", sent_at=utcnow())
            return
        log.warning("alert %s: email to %s FAILED after %d attempts: %s", alert.alert_id, email,
                    len(self.retry_waits), last_error)
        await self._mark(notification_id, status="FAILED")

    async def _mark(self, notification_id: int, **values) -> None:
        async with self.session_factory() as session:
            await session.execute(update(Notification).where(Notification.notification_id == notification_id)
                                  .values(**values))
            await session.commit()
