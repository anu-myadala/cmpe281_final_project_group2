"""ReportService (BR-08, BR-09): four report types behind the ReportType
interface, JSON or streamed CSV. The count query answers BR-09 with a 422
before the response starts; rows are then produced in batches of 1,000."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.rbac import Scope, vehicle_in_scope, vehicles_of_scope
from ..core.timeutil import iso
from ..errors import BusinessRule, NotFound
from ..models import ALERT_TYPES, sound_class_name
from ..repos import AlertSummaryRepo
from .dashboard_service import pick_granularity

ROW_CAP = 50_000
BATCH_SIZE = 1000


class ReportContext:
    def __init__(self, container: AppContainer, session: AsyncSession, scope: Scope, from_: datetime, to: datetime,
                 vehicle_code: Optional[str], vehicle_codes: Optional[List[str]]) -> None:
        self.c = container
        self.session = session
        self.scope = scope
        self.from_ = from_
        self.to = to
        self.vehicle_code = vehicle_code
        self.vehicle_codes = vehicle_codes  # RIDER restriction, else None


class ReportType:
    name = ""
    columns: List[str] = []

    async def count(self, ctx: ReportContext) -> int:
        raise NotImplementedError

    async def query(self, ctx: ReportContext, batch_size: int = BATCH_SIZE) -> AsyncIterator[List[List[Any]]]:
        raise NotImplementedError
        yield  # pragma: no cover


class AlertsByDayReport(ReportType):
    name = "alerts-by-day"
    columns = ["day"] + list(ALERT_TYPES) + ["total"]

    async def count(self, ctx: ReportContext) -> int:
        return (ctx.to.date() - ctx.from_.date()).days + 1

    async def query(self, ctx: ReportContext, batch_size: int = BATCH_SIZE):
        rows = await AlertSummaryRepo.rows(ctx.session, ctx.scope.tenant_id, ctx.from_, ctx.to, ctx.vehicle_code,
                                           ctx.vehicle_codes)
        days: Dict[str, Dict[str, int]] = {}
        day = ctx.from_.date()
        while day <= ctx.to.date():
            days[day.isoformat()] = {t: 0 for t in ALERT_TYPES}
            day += timedelta(days=1)
        for r in rows:
            entry = days.setdefault(r["created_at"].date().isoformat(), {t: 0 for t in ALERT_TYPES})
            if r["alert_type_code"] in entry:
                entry[r["alert_type_code"]] += 1
        out = [[d] + [counts[t] for t in ALERT_TYPES] + [sum(counts.values())] for d, counts in sorted(days.items())]
        for i in range(0, len(out), batch_size):
            yield out[i:i + batch_size]


class EventsByClassReport(ReportType):
    name = "events-by-class"
    columns = ["class", "count", "meanConfidence", "lastEventAt"]

    async def _rows(self, ctx: ReportContext) -> List[List[Any]]:
        rows = await ctx.c.events.events_by_class(ctx.scope.tenant_code, ctx.from_, ctx.to, ctx.vehicle_code,
                                                  vehicle_codes=ctx.vehicle_codes)
        return [[r["class"], r["count"], r["meanConf"], iso(r["lastEventAt"])] for r in rows]

    async def count(self, ctx: ReportContext) -> int:
        return len(await self._rows(ctx))

    async def query(self, ctx: ReportContext, batch_size: int = BATCH_SIZE):
        rows = await self._rows(ctx)
        for i in range(0, len(rows), batch_size):
            yield rows[i:i + batch_size]


class VehicleActivityReport(ReportType):
    name = "vehicle-activity"
    columns = ["vehicleId", "label", "status", "events", "topClass", "lastEventAt", "alerts", "openAlerts", "falseAlarms"]

    async def _rows(self, ctx: ReportContext) -> List[List[Any]]:
        vehicles = await vehicles_of_scope(ctx.session, ctx.scope, include_retired=True)
        if ctx.vehicle_code:
            vehicles = [v for v in vehicles if v.vehicle_code == ctx.vehicle_code]
        activity = await ctx.c.events.vehicle_activity(ctx.scope.tenant_code, ctx.from_, ctx.to)
        alerts = await AlertSummaryRepo.per_vehicle(ctx.session, ctx.scope.tenant_id, ctx.from_, ctx.to)
        out = []
        for v in vehicles:
            act = activity.get(v.vehicle_code, {})
            al = alerts.get(v.vehicle_code, {})
            out.append([v.vehicle_code, v.label, v.status, act.get("events", 0), act.get("topClass"),
                        iso(act.get("lastEventAt")), al.get("alerts", 0), al.get("openAlerts", 0), al.get("falseAlarms", 0)])
        return out

    async def count(self, ctx: ReportContext) -> int:
        return len(await self._rows(ctx))

    async def query(self, ctx: ReportContext, batch_size: int = BATCH_SIZE):
        rows = await self._rows(ctx)
        for i in range(0, len(rows), batch_size):
            yield rows[i:i + batch_size]


class FalseAlarmReport(ReportType):
    name = "false-alarms"
    columns = ["alertId", "vehicleId", "type", "severity", "topClass", "confidence", "createdAt", "closedAt"]

    async def count(self, ctx: ReportContext) -> int:
        return await AlertSummaryRepo.count(ctx.session, ctx.scope.tenant_id, ctx.from_, ctx.to, ctx.vehicle_code,
                                            ctx.vehicle_codes, statuses=["FALSE_ALARM"])

    async def query(self, ctx: ReportContext, batch_size: int = BATCH_SIZE):
        offset = 0
        while True:
            rows = await AlertSummaryRepo.rows(ctx.session, ctx.scope.tenant_id, ctx.from_, ctx.to, ctx.vehicle_code,
                                               ctx.vehicle_codes, statuses=["FALSE_ALARM"], offset=offset,
                                               limit=batch_size)
            if not rows:
                return
            yield [[r["alert_id"], r["vehicle_id"], r["alert_type_code"], r["severity"],
                    sound_class_name(r["top_class_id"]), float(r["confidence"]) if r["confidence"] is not None else None,
                    iso(r["created_at"]), iso(r["closed_at"])] for r in rows]
            if len(rows) < batch_size:
                return
            offset += batch_size


REPORT_TYPES = {r.name: r for r in (AlertsByDayReport(), EventsByClassReport(), VehicleActivityReport(), FalseAlarmReport())}


def csv_line(values: List[Any]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerow(["" if v is None else v for v in values])
    return buffer.getvalue()


class ReportService:
    def __init__(self, container: AppContainer, session: AsyncSession) -> None:
        self.c = container
        self.session = session

    async def _context(self, report_type: str, scope: Scope, from_: datetime, to: datetime,
                       vehicle_code: Optional[str]) -> tuple:
        if report_type not in REPORT_TYPES:
            raise NotFound(f"Unknown report type {report_type}")
        pick_granularity(from_, to)  # BR-08 range check before any query
        if vehicle_code:
            await vehicle_in_scope(self.session, scope, vehicle_code)  # 404 outside the scope
        codes = None
        if scope.vehicle_ids is not None:
            codes = [v.vehicle_code for v in await vehicles_of_scope(self.session, scope, include_retired=True)]
        return REPORT_TYPES[report_type], ReportContext(self.c, self.session, scope, from_, to, vehicle_code, codes)

    async def run(self, report_type: str, scope: Scope, from_: datetime, to: datetime,
                  vehicle_code: Optional[str] = None) -> Dict[str, Any]:
        report, ctx = await self._context(report_type, scope, from_, to, vehicle_code)
        rows: List[List[Any]] = []
        async for batch in report.query(ctx):
            rows.extend(batch)
        return {"type": report.name, "columns": list(report.columns), "rows": rows, "total": len(rows),
                "from": iso(from_), "to": iso(to)}

    async def export_csv(self, report_type: str, scope: Scope, from_: datetime, to: datetime,
                         vehicle_code: Optional[str] = None) -> StreamingResponse:
        report, ctx = await self._context(report_type, scope, from_, to, vehicle_code)
        if await report.count(ctx) > ROW_CAP:  # BR-09 before any byte is sent
            raise BusinessRule("BR-09", "Narrow the range: more than 50,000 rows", maxRows=ROW_CAP)

        async def generate():
            # The request session is closed once the handler returns, so the
            # stream reads its batches through a session of its own.
            async with self.c.session_factory() as session:
                ctx.session = session
                yield csv_line(report.columns)
                async for batch in report.query(ctx, BATCH_SIZE):
                    yield "".join(csv_line(row) for row in batch)

        name = f"{report.name}_{from_:%Y%m%d}_{to:%Y%m%d}.csv"
        return StreamingResponse(generate(), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})
