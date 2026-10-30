"""AuditLogger (BR-12): one audit_log row per audited action with the
before and after values. The row is added to the caller's session so that it
commits (or rolls back) with the write it describes. The class is shared with
C2 and C3, which pass their own action names (RETIRE_VEHICLE, RULE_SAVE, ...).
"""
from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog

SENSITIVE_KEYS = {"password_hash", "password", "token_hash", "temporary_password"}


def _jsonable(value: Any) -> Any:
    """Make a dict JSON-safe and strip secrets."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if k not in SENSITIVE_KEYS}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "__table__"):  # SQLAlchemy row -> column dict
        return _jsonable({c.key: getattr(value, c.key) for c in value.__table__.columns})
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def snapshot(row: Any) -> Optional[dict]:
    return _jsonable(row) if row is not None else None


class AuditLogger:
    @staticmethod
    def write(session: AsyncSession, actor: Optional[int], tenant_id: Optional[int], action: str, entity: str,
              entity_id: Any, before: Any, after: Any, ip: Optional[str] = None) -> AuditLog:
        row = AuditLog(actor_user_id=actor, tenant_id=tenant_id, action=action, entity=entity,
                       entity_id=str(entity_id) if entity_id is not None else None,
                       before_json=_jsonable(before) if before is not None else None,
                       after_json=_jsonable(after) if after is not None else None, ip=ip)
        session.add(row)
        return row
