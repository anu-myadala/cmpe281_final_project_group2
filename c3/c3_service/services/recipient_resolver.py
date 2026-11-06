"""RecipientResolver: who hears about a new alert, and on which channel (Part III, Section 4.3,
Table 104; the notification half of C1's BR-05; BR-C3-11).

    Table 104      N1   N2   N3   N4
    EMAIL selected  N    Y    Y    Y
    quiet hours     -    Y    N    N
    rider active    -    -    N    Y
    dashboard       Y    Y    Y    Y
    email staff     -    -    Y    Y     (OWNER and FLEET_MANAGER users of the tenant)
    email riders    -    -    -    Y     (only when notify_riders is on)

The decision is pure functions over plain values (in_quiet_hours, channels_for, resolve),
so every column is one unit test. RecipientResolver.for_alert() only loads those values:
the subscription, the tenant's time zone, the users and the riders. SMS and PUSH are design
only (Table 94), so they produce no recipients.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from ..models import Alert, AppUser, Subscription, Tenant, Vehicle, VehicleRider

log = logging.getLogger("scicp.c3.recipients")

STAFF_ROLES = ("OWNER", "FLEET_MANAGER")
DASHBOARD_RECIPIENT = "dashboard"  # how the one DASHBOARD row shows in notifications[]: the tenant's room, not a person


@dataclass(frozen=True)
class Person:
    user_id: int
    email: Optional[str]
    active: bool = True


@dataclass(frozen=True)
class Recipient:
    channel: str                 # DASHBOARD | EMAIL
    user_id: Optional[int]       # None for the DASHBOARD row
    email: Optional[str]


@dataclass(frozen=True)
class Channels:
    """One column of Table 104."""
    dashboard: bool
    email_staff: bool
    email_riders: bool


# -- pure decisions -------------------------------------------------------------------------------
def local_time_of(now_utc: datetime, time_zone: str) -> datetime:
    """naive-UTC `now` in the tenant's time zone; an unknown zone falls back to UTC (logged)."""
    aware = now_utc.replace(tzinfo=timezone.utc) if now_utc.tzinfo is None else now_utc
    try:
        return aware.astimezone(ZoneInfo(time_zone))
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown time zone %r: quiet hours are evaluated in UTC", time_zone)
        return aware


def in_quiet_hours(local: time, quiet_from: Optional[time], quiet_to: Optional[time]) -> bool:
    """[quiet_from, quiet_to) in local time; crosses midnight when quiet_from > quiet_to (22:00 to 06:00).
    Without both ends, or with equal ends (C1 refuses that, R3), there are no quiet hours."""
    if quiet_from is None or quiet_to is None or quiet_from == quiet_to:
        return False
    local = local.replace(tzinfo=None)
    if quiet_from < quiet_to:
        return quiet_from <= local < quiet_to
    return local >= quiet_from or local < quiet_to


def channels_for(email_selected: bool, quiet: bool, riders_active: bool) -> Channels:
    """Table 104: N1 (no EMAIL), N2 (quiet hours), N3 (email the staff), N4 (and the riders)."""
    if not email_selected or quiet:
        return Channels(dashboard=True, email_staff=False, email_riders=False)          # N1, N2
    return Channels(dashboard=True, email_staff=True, email_riders=riders_active)      # N3, N4


def resolve(channels_csv: str, quiet_from: Optional[time], quiet_to: Optional[time], notify_riders: bool,
            now_utc: datetime, time_zone: str, staff: Iterable[Person], riders: Iterable[Person]) -> List[Recipient]:
    """The DASHBOARD row always; then one EMAIL row per distinct active user with an email address."""
    selected = {c.strip().upper() for c in (channels_csv or "").split(",") if c.strip()}
    quiet = in_quiet_hours(local_time_of(now_utc, time_zone).time(), quiet_from, quiet_to)
    reachable_riders = [p for p in riders if p.active and p.email] if notify_riders else []
    columns = channels_for("EMAIL" in selected, quiet, bool(reachable_riders))
    out = [Recipient("DASHBOARD", None, None)]
    people: List[Person] = []
    if columns.email_staff:
        people += list(staff)
    if columns.email_riders:
        people += reachable_riders
    seen = set()
    for person in people:
        if person.active and person.email and person.user_id not in seen:
            seen.add(person.user_id)
            out.append(Recipient("EMAIL", person.user_id, person.email))
    return out


# -- loading the values ---------------------------------------------------------------------------------
class RecipientResolver:
    def __init__(self, session) -> None:
        self.session = session

    async def for_alert(self, alert: Alert, now_utc: datetime) -> Tuple[List[Recipient], str]:
        """(recipients, the tenant's time zone). The alert only exists when its service is on, so a missing
        subscription row is unusual; it is treated as dashboard only."""
        tenant_zone = (await self.session.execute(select(Tenant.time_zone).where(
            Tenant.tenant_id == alert.tenant_id))).scalar_one_or_none() or "UTC"
        vehicle_id = (await self.session.execute(select(Vehicle.vehicle_id).where(
            Vehicle.tenant_id == alert.tenant_id, Vehicle.vehicle_code == alert.vehicle_id))).scalar_one_or_none()
        sub = None
        if vehicle_id is not None:
            sub = (await self.session.execute(select(Subscription).where(
                Subscription.vehicle_id == vehicle_id, Subscription.service_code == alert.alert_type_code))).scalars().first()
        if sub is None:
            return [Recipient("DASHBOARD", None, None)], tenant_zone
        staff = [Person(uid, email, status == "ACTIVE") for uid, email, status in (await self.session.execute(
            select(AppUser.user_id, AppUser.email, AppUser.status).where(
                AppUser.tenant_id == alert.tenant_id, AppUser.role.in_(STAFF_ROLES))
            .order_by(AppUser.user_id))).all()]
        riders = [Person(uid, email, status == "ACTIVE") for uid, email, status in (await self.session.execute(
            select(AppUser.user_id, AppUser.email, AppUser.status)
            .join(VehicleRider, VehicleRider.user_id == AppUser.user_id)
            .where(VehicleRider.vehicle_id == vehicle_id, VehicleRider.valid_from <= now_utc,
                   (VehicleRider.valid_to.is_(None)) | (VehicleRider.valid_to > now_utc))
            .order_by(AppUser.user_id))).all()]
        recipients = resolve(sub.channels, sub.quiet_from, sub.quiet_to, sub.notify_riders, now_utc, tenant_zone,
                             staff, riders)
        return recipients, tenant_zone
