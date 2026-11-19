"""BillingService (report section 5.1, a system function component service).

Every tenant pays the monthly fee of its plan (BASIC, FLEET, ENTERPRISE) and usage over the plan's
allowances. The usage of a month is measured from the platform's own data:

    vehicles          vehicle rows of the tenant that are not RETIRED              MySQL (C2's table)
    clips classified  audio_events documents of the tenant in the month            MongoDB (C4 writes them)
    emails sent       notification rows, channel EMAIL, status SENT, in the month  MySQL (C3's table)
    alerts            alert rows in the month (shown, included in every plan)      MySQL (C3's table)
    clip storage      size of the tenant's STORED clips, in GB                     MySQL (C2's table)

    invoice = monthly fee
            + extra vehicles x price per extra vehicle
            + ceil(clips over the allowance / 1,000) x price per 1,000 clips
            + ceil(emails over the allowance / 100) x price per 100 emails
            + GB stored x price per GB-month

Amounts are integer US cents. For the running month the invoice is "to date" and also projected to
the end of the month from the usage so far. When C2 or C3 is not installed (C1 alone with the
stand-ins), their tables are missing: that usage counts as 0 and the invoice says so.
"""
from __future__ import annotations

import calendar
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.audit import AuditLogger, snapshot
from ..core.timeutil import iso, utcnow
from ..errors import NotFound, Validation
from ..models import PLANS, Alert, PriceList, Tenant, Vehicle
from ..repos import EventQueryRepo
from ..schemas import PriceBody

log = logging.getLogger("scicp.billing")

# The price list the platform starts with (US cents). The admin can change it on the billing page.
DEFAULT_PRICES: Dict[str, Dict[str, int]] = {
    "BASIC": {"monthly_fee_cents": 999, "included_vehicles": 3, "extra_vehicle_cents": 0,
              "included_clips": 10_000, "per_1000_clips_cents": 50, "included_emails": 500,
              "per_100_emails_cents": 100, "storage_gb_cents": 10},
    "FLEET": {"monthly_fee_cents": 19_900, "included_vehicles": 10, "extra_vehicle_cents": 1_200,
              "included_clips": 100_000, "per_1000_clips_cents": 40, "included_emails": 5_000,
              "per_100_emails_cents": 80, "storage_gb_cents": 8},
    "ENTERPRISE": {"monthly_fee_cents": 149_900, "included_vehicles": 100, "extra_vehicle_cents": 900,
                   "included_clips": 2_000_000, "per_1000_clips_cents": 30, "included_emails": 100_000,
                   "per_100_emails_cents": 50, "storage_gb_cents": 5},
}

FIELDS = {  # API name -> column
    "monthlyFeeCents": "monthly_fee_cents", "includedVehicles": "included_vehicles",
    "extraVehicleCents": "extra_vehicle_cents", "includedClips": "included_clips",
    "per1000ClipsCents": "per_1000_clips_cents", "includedEmails": "included_emails",
    "per100EmailsCents": "per_100_emails_cents", "storageGbCents": "storage_gb_cents",
}

GB = 1024 ** 3


def price_dto(row: PriceList) -> Dict[str, Any]:
    out: Dict[str, Any] = {"plan": row.plan}
    out.update({api: getattr(row, column) for api, column in FIELDS.items()})
    out["updatedAt"] = iso(row.updated_at)
    return out


def month_range(month: Optional[str], now: datetime) -> Tuple[str, datetime, datetime, datetime, bool]:
    """'2026-11' -> (label, first instant, first instant of the next month, end of the measured span,
    whether the month is still running). Default: the current month."""
    if month:
        try:
            year, mon = (int(part) for part in month.split("-"))
            start = datetime(year, mon, 1)
        except (ValueError, TypeError):
            raise Validation("month must be YYYY-MM", {"field": "month"})
    else:
        start = datetime(now.year, now.month, 1)
    days = calendar.monthrange(start.year, start.month)[1]
    nxt = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1)
    if start > now:
        raise Validation("month is in the future", {"field": "month"})
    running = now < nxt
    return f"{start.year:04d}-{start.month:02d}", start, nxt, (now if running else nxt), running


def compute_invoice(price: Dict[str, int], usage: Dict[str, Any], fraction: float = 1.0) -> Dict[str, Any]:
    """Line items and totals in cents. `fraction` (0..1] is the part of the month measured; the
    projection scales the usage that grows with time (clips, emails) to the whole month."""
    def lines_for(clips: int, emails: int) -> List[Dict[str, Any]]:
        vehicles = int(usage["vehicles"])
        extra_vehicles = max(0, vehicles - price["included_vehicles"])
        clip_blocks = math.ceil(max(0, clips - price["included_clips"]) / 1000)
        email_blocks = math.ceil(max(0, emails - price["included_emails"]) / 100)
        storage_gb = float(usage["storageGb"] or 0.0)
        return [
            {"code": "PLAN", "description": "Monthly plan fee", "quantity": 1, "unit": "month",
             "unitCents": price["monthly_fee_cents"], "amountCents": price["monthly_fee_cents"]},
            {"code": "VEHICLES", "description": f"Vehicles over the {price['included_vehicles']} included",
             "quantity": extra_vehicles, "unit": "vehicle", "unitCents": price["extra_vehicle_cents"],
             "amountCents": extra_vehicles * price["extra_vehicle_cents"]},
            {"code": "CLIPS", "description": f"Clips classified over the {price['included_clips']:,} included",
             "quantity": clip_blocks, "unit": "1,000 clips", "unitCents": price["per_1000_clips_cents"],
             "amountCents": clip_blocks * price["per_1000_clips_cents"]},
            {"code": "EMAILS", "description": f"Alert emails over the {price['included_emails']:,} included",
             "quantity": email_blocks, "unit": "100 emails", "unitCents": price["per_100_emails_cents"],
             "amountCents": email_blocks * price["per_100_emails_cents"]},
            {"code": "STORAGE", "description": "Clip storage", "quantity": round(storage_gb, 3), "unit": "GB-month",
             "unitCents": price["storage_gb_cents"], "amountCents": int(round(storage_gb * price["storage_gb_cents"]))},
        ]

    lines = lines_for(int(usage["clips"]), int(usage["emails"]))
    total = sum(line["amountCents"] for line in lines)
    projected = total
    if fraction < 1.0:
        scale = 1.0 / max(fraction, 1e-6)
        projected = sum(line["amountCents"] for line in lines_for(int(round(usage["clips"] * scale)),
                                                                   int(round(usage["emails"] * scale))))
    return {"lines": lines, "totalCents": total, "projectedTotalCents": projected}


class BillingService:
    def __init__(self, container: AppContainer, session: AsyncSession) -> None:
        self.c = container
        self.session = session

    # -- price list -------------------------------------------------------------------------------
    async def _ensure_prices(self) -> Dict[str, PriceList]:
        rows = {r.plan: r for r in (await self.session.execute(select(PriceList))).scalars()}
        missing = [plan for plan in PLANS if plan not in rows]
        if missing:
            for plan in missing:
                self.session.add(PriceList(plan=plan, **DEFAULT_PRICES[plan]))
            try:
                await self.session.commit()
            except IntegrityError:  # another replica or request inserted them at the same moment
                await self.session.rollback()
                log.info("billing: default prices inserted concurrently; using the stored rows")
            rows = {r.plan: r for r in (await self.session.execute(select(PriceList))).scalars()}
        return rows

    async def prices(self) -> List[Dict[str, Any]]:
        rows = await self._ensure_prices()
        return [price_dto(rows[plan]) for plan in PLANS]

    async def update_price(self, plan: str, body: PriceBody, actor: int, ip: Optional[str]) -> Dict[str, Any]:
        if plan not in PLANS:
            raise NotFound("Plan not found")
        rows = await self._ensure_prices()
        row = rows[plan]
        before = snapshot(row)
        changes = body.model_dump(exclude_none=True)
        if not changes:
            raise Validation("Nothing to change", {"field": "body"})
        for api, value in changes.items():
            setattr(row, FIELDS[api], int(value))
        row.updated_by = actor
        row.updated_at = utcnow()
        await self.session.flush()
        AuditLogger.write(self.session, actor, None, "PRICE_UPDATE", "price_list", plan, before, snapshot(row), ip)
        await self.session.commit()
        return price_dto(row)

    # -- usage ------------------------------------------------------------------------------------
    async def _optional_scalar(self, sql: str, params: Dict[str, Any]) -> Optional[float]:
        """A query on a table of C2 or C3; None when that table does not exist (C1 alone)."""
        try:
            async with self.c.engine.connect() as conn:
                value = (await conn.execute(text(sql), params)).scalar()
            return float(value or 0)
        except Exception as exc:  # noqa: BLE001 - missing table or column: usage not measured
            log.info("billing: %s not available (%s)", sql.split("FROM", 1)[-1].split()[0], exc.__class__.__name__)
            return None

    async def usage(self, tenant: Tenant, start: datetime, end: datetime) -> Dict[str, Any]:
        vehicles = (await self.session.execute(
            select(func.count()).select_from(Vehicle).where(Vehicle.tenant_id == tenant.tenant_id,
                                                            Vehicle.status != "RETIRED"))).scalar_one()
        alerts = (await self.session.execute(
            select(func.count()).select_from(Alert).where(Alert.tenant_id == tenant.tenant_id,
                                                          Alert.created_at >= start, Alert.created_at < end))).scalar_one()
        try:
            clips = await EventQueryRepo(self.c.mongo).count_events(tenant.code, start, end)
        except Exception as exc:  # noqa: BLE001
            log.warning("billing: audio_events not readable: %s", exc)
            clips = 0
        emails = await self._optional_scalar(
            "SELECT COUNT(*) FROM notification WHERE tenant_id = :t AND channel = 'EMAIL' AND status = 'SENT' "
            "AND created_at >= :s AND created_at < :e", {"t": tenant.tenant_id, "s": start, "e": end})
        stored = await self._optional_scalar(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM clip WHERE tenant_id = :t AND state = 'STORED'",
            {"t": tenant.tenant_id})
        measured = [name for name, value in (("emails", emails), ("storage", stored)) if value is None]
        return {"vehicles": int(vehicles), "alerts": int(alerts), "clips": int(clips),
                "emails": int(emails or 0), "storageGb": round((stored or 0) / GB, 4),
                "notMeasured": measured}

    # -- invoices ---------------------------------------------------------------------------------
    async def invoice(self, tenant_id: int, month: Optional[str]) -> Dict[str, Any]:
        # The price list first: inserting the defaults may roll back (another replica inserted them at
        # the same moment), and a rollback expires every object already loaded in the session.
        prices = await self._ensure_prices()
        tenant = await self.session.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFound("Tenant not found")
        now = utcnow()
        label, start, nxt, end, running = month_range(month, now)
        usage = await self.usage(tenant, start, end)
        price = {column: getattr(prices[tenant.plan], column) for column in FIELDS.values()}
        fraction = (end - start).total_seconds() / (nxt - start).total_seconds()
        bill = compute_invoice(price, usage, fraction if running else 1.0)
        return {"tenantId": tenant.tenant_id, "tenant": tenant.name, "tenantCode": tenant.code, "plan": tenant.plan,
                "month": label, "from": iso(start), "to": iso(end), "running": running,
                "currency": "USD", "usage": usage,
                "allowances": {"vehicles": price["included_vehicles"], "clips": price["included_clips"],
                               "emails": price["included_emails"]},
                **bill}

    async def summary(self, month: Optional[str]) -> Dict[str, Any]:
        await self._ensure_prices()  # before loading the tenants (see invoice)
        tenants = list((await self.session.execute(select(Tenant).order_by(Tenant.tenant_id))).scalars())
        rows = []
        for tenant in tenants:
            inv = await self.invoice(tenant.tenant_id, month)
            rows.append({"tenantId": tenant.tenant_id, "tenant": tenant.name, "plan": tenant.plan,
                         "status": tenant.status, "vehicles": inv["usage"]["vehicles"],
                         "clips": inv["usage"]["clips"], "emails": inv["usage"]["emails"],
                         "alerts": inv["usage"]["alerts"], "totalCents": inv["totalCents"],
                         "projectedTotalCents": inv["projectedTotalCents"]})
        label, _, _, _, running = month_range(month, utcnow())
        return {"month": label, "running": running, "currency": "USD", "tenants": rows,
                "totalCents": sum(r["totalCents"] for r in rows if r["status"] == "ACTIVE"),
                "projectedTotalCents": sum(r["projectedTotalCents"] for r in rows if r["status"] == "ACTIVE")}
