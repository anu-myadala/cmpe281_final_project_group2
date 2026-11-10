"""UTC time helpers. The databases store naive UTC datetimes; the API exchanges
ISO-8601 strings such as 2026-11-04T21:42:07Z or 2026-11-04T21:42:07.312Z, in exactly
the format of C1's app/core/timeutil.py so that the web app sorts and parses them alike."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Optional

from .errors import Validation


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_naive_utc(value: Any) -> Optional[datetime]:
    """MongoDB drivers may return aware datetimes; the SQL rows are naive UTC. Non-dates give None."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    value = as_naive_utc(value)
    if value.microsecond:
        return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str, field: str = "timestamp") -> datetime:
    """An ISO-8601 timestamp or a plain date as naive UTC; 400 VALIDATION otherwise."""
    raw = value.strip()
    try:
        if len(raw) == 10:
            return datetime.combine(date.fromisoformat(raw), time.min)
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise Validation(f"{field} is not an ISO-8601 timestamp", {"field": field})
    return as_naive_utc(parsed)
