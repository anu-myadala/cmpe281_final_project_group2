"""AlertSummaryRepo: read-only access to the C3 view v_alert_summary for the
overview counts, the statistics page and the reports. The view is mapped on
its own MetaData so that create_all never tries to create it as a table."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import Column, DateTime, Float, Integer, MetaData, SmallInteger, String, Table, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import OPEN_STATUSES

view_metadata = MetaData()

v_alert_summary = Table(
    "v_alert_summary", view_metadata,
    Column("alert_id", Integer, primary_key=True),
    Column("tenant_id", Integer),
    Column("vehicle_id", String(32)),
    Column("alert_type_code", String(24)),
    Column("severity", String(16)),
    Column("status", String(16)),
    Column("top_class_id", SmallInteger),
    Column("confidence", Float),
    Column("created_at", DateTime),
    Column("acknowledged_at", DateTime),
    Column("closed_at", DateTime),
)

V = v_alert_summary.c


class AlertSummaryRepo:
    @staticmethod
    def _scoped(tenant_id: int, from_: Optional[datetime] = None, to: Optional[datetime] = None,
                vehicle_code: Optional[str] = None, vehicle_codes: Optional[Sequence[str]] = None):
        conditions = [V.tenant_id == tenant_id]
        if from_ is not None:
            conditions.append(V.created_at >= from_)
        if to is not None:
            conditions.append(V.created_at < to)
        if vehicle_code:
            conditions.append(V.vehicle_id == vehicle_code)
        if vehicle_codes is not None:
            conditions.append(V.vehicle_id.in_(list(vehicle_codes) or ["-"]))
        return conditions

    @classmethod
    async def count(cls, session: AsyncSession, tenant_id: int, from_=None, to=None, vehicle_code=None,
                    vehicle_codes=None, statuses: Optional[Sequence[str]] = None) -> int:
        stmt = select(func.count()).select_from(v_alert_summary).where(*cls._scoped(tenant_id, from_, to, vehicle_code, vehicle_codes))
        if statuses:
            stmt = stmt.where(V.status.in_(list(statuses)))
        return int((await session.execute(stmt)).scalar_one())

    @classmethod
    async def open_count_per_vehicle(cls, session: AsyncSession, tenant_id: int) -> Dict[str, int]:
        rows = await session.execute(select(V.vehicle_id, func.count()).where(
            V.tenant_id == tenant_id, V.status.in_(list(OPEN_STATUSES))).group_by(V.vehicle_id))
        return {code: int(c) for code, c in rows}

    @classmethod
    async def rows(cls, session: AsyncSession, tenant_id: int, from_: datetime, to: datetime,
                   vehicle_code: Optional[str] = None, vehicle_codes=None, statuses: Optional[Sequence[str]] = None,
                   offset: int = 0, limit: Optional[int] = None) -> List:
        stmt = select(v_alert_summary).where(*cls._scoped(tenant_id, from_, to, vehicle_code, vehicle_codes))
        if statuses:
            stmt = stmt.where(V.status.in_(list(statuses)))
        stmt = stmt.order_by(V.created_at, V.alert_id).offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await session.execute(stmt)).mappings())

    @classmethod
    async def stats(cls, session: AsyncSession, tenant_id: int, from_: datetime, to: datetime,
                    vehicle_codes=None) -> Tuple[List, float, Optional[float]]:
        """Rows of the range plus the false-alarm rate and the mean acknowledge time."""
        rows = await cls.rows(session, tenant_id, from_, to, vehicle_codes=vehicle_codes)
        total = len(rows)
        false_alarms = sum(1 for r in rows if r["status"] == "FALSE_ALARM")
        ack_seconds = [(r["acknowledged_at"] - r["created_at"]).total_seconds()
                       for r in rows if r["acknowledged_at"] is not None]
        rate = round(false_alarms / total, 4) if total else 0.0
        mean_ack = round(sum(ack_seconds) / len(ack_seconds), 1) if ack_seconds else None
        return rows, rate, mean_ack

    @classmethod
    async def per_vehicle(cls, session: AsyncSession, tenant_id: int, from_: datetime, to: datetime) -> Dict[str, Dict[str, int]]:
        rows = await session.execute(select(V.vehicle_id, V.status, func.count()).where(
            *cls._scoped(tenant_id, from_, to)).group_by(V.vehicle_id, V.status))
        out: Dict[str, Dict[str, int]] = {}
        for code, status, count in rows:
            entry = out.setdefault(code, {"alerts": 0, "openAlerts": 0, "falseAlarms": 0})
            entry["alerts"] += int(count)
            if status in OPEN_STATUSES:
                entry["openAlerts"] += int(count)
            if status == "FALSE_ALARM":
                entry["falseAlarms"] += int(count)
        return out
