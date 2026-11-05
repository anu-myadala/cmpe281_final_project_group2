"""notification: one row per recipient and channel of an alert, with the outcome.

Design source: Deliverable #2, Part III, Section 3.3.1, Table 94 (row notification:
PK notification_id, FK alert_id and recipient_user_id, indexes (alert_id) and
(status, created_at); "DASHBOARD rows are SENT at once; SMS and PUSH are design only;
email address comes from app_user"), the notification state diagram of Section 3.4.1
(Table 99: PENDING -> SENT or FAILED after three attempts), objective O4 of Table 77,
and the recipients of Table 104. Written from step 8 on. GET /alerts/{id} reports it
as notifications[].
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Enum, ForeignKey, Index, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigId, UtcDateTime, utcnow

CHANNELS = ("DASHBOARD", "EMAIL", "SMS", "PUSH")
NOTIFICATION_STATUSES = ("PENDING", "SENT", "FAILED")


class Notification(Base):
    __tablename__ = "notification"
    __table_args__ = (
        Index("ix_notif_alert", "alert_id"),
        Index("ix_notif_status", "status", "created_at"),
    )

    notification_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alert.alert_id", name="fk_notif_alert"), nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigId, nullable=False)
    # NULL for the DASHBOARD row, which goes to the tenant's room rather than to one user.
    recipient_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("app_user.user_id", name="fk_notif_user"),
                                                             nullable=True)
    channel: Mapped[str] = mapped_column(Enum(*CHANNELS, name="notif_channel"), nullable=False)
    status: Mapped[str] = mapped_column(Enum(*NOTIFICATION_STATUSES, name="notif_status"), nullable=False,
                                        default="PENDING")
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    sent_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
