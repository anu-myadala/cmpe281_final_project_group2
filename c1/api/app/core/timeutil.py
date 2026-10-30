"""ISO-8601 UTC helpers. The database stores naive UTC datetimes; the API
exchanges strings like 2026-11-04T21:42:07Z."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from ..errors import Validation
from ..models import utcnow

__all__ = ["utcnow", "iso", "parse_iso", "parse_time", "time_str", "parse_range"]


def iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    if value.microsecond:
        return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str, field: str = "timestamp") -> datetime:
    """Parse an ISO-8601 timestamp or date into a naive UTC datetime."""
    raw = value.strip()
    try:
        if len(raw) == 10:
            return datetime.combine(date.fromisoformat(raw), time.min)
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise Validation(f"{field} is not an ISO-8601 timestamp", {"field": field})
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def parse_time(value: Optional[str], field: str) -> Optional[time]:
    if value is None or value == "":
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        raise Validation(f"{field} must be HH:MM", {"field": field})


def time_str(value: Optional[time]) -> Optional[str]:
    return value.strftime("%H:%M") if value is not None else None


def parse_range(from_: Optional[str], to: Optional[str], default_days: int = 7) -> tuple:
    """from/to query parameters; defaults to the last `default_days` days."""
    now = utcnow()
    end = parse_iso(to, "to") if to else now
    start = parse_iso(from_, "from") if from_ else end - timedelta(days=default_days)
    if start > end:
        raise Validation("from must not be after to", {"field": "from"})
    return start, end
