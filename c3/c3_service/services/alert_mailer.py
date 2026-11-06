"""AlertMailer: the alert email (Part III, Table 88 "AlertMailer"; BR-C3-11).

build() makes one message with a plain-text and an HTML part: the subject
"[SCICP] <type> alert on <vehicle>: <class> (<severity>)", the vehicle, type, severity,
class, confidence, the time in the tenant's time zone, and the link to the alert page
{PUBLIC_BASE_URL}/alerts/{alertId}. send() delivers it like C1's MailSender: MAIL_MODE=smtp
through smtplib in a worker thread (MailHog in docker compose), MAIL_MODE=log to the log.
C3 has no SES mode (team decision: no boto3 in C3), so MAIL_MODE=ses fails the attempt
with a clear error. Unlike C1, send() raises on failure, so NotificationService can count
the attempts and store last_error.
"""
from __future__ import annotations

import asyncio
import html
import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

from ..config import Settings
from ..models import Alert, sound_class_name
from .recipient_resolver import local_time_of

log = logging.getLogger("scicp.c3.mail")

SMTP_TIMEOUT_S = 5  # as C1's MailSender


class MailError(Exception):
    """A send attempt failed; the message is stored as notification.last_error."""


class AlertMailer:
    def __init__(self, settings: Settings) -> None:
        self.mode = settings.mail_mode
        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        self.mail_from = settings.mail_from
        self.base_url = settings.public_base_url.rstrip("/")

    def link(self, alert: Alert) -> str:
        return f"{self.base_url}/alerts/{alert.alert_id}"

    def build(self, alert: Alert, recipient: str, time_zone: str = "UTC") -> EmailMessage:
        cls = sound_class_name(alert.top_class_id) or "unknown sound"
        when = local_time_of(alert.created_at, time_zone).strftime("%Y-%m-%d %H:%M:%S %Z")
        confidence = f"{float(alert.confidence):.2f}" if alert.confidence is not None else "-"
        facts = [("Vehicle", alert.vehicle_id), ("Alert type", alert.alert_type_code), ("Severity", alert.severity),
                 ("Sound class", cls), ("Confidence", confidence), ("Time", f"{when} ({time_zone})")]
        link = self.link(alert)
        message = EmailMessage()
        message["From"] = self.mail_from
        message["To"] = recipient
        message["Subject"] = f"[SCICP] {alert.alert_type_code} alert on {alert.vehicle_id}: {cls} ({alert.severity})"
        text = "\n".join([f"A new {alert.alert_type_code} alert was raised on {alert.vehicle_id}.", ""]
                         + [f"{name}: {value}" for name, value in facts]
                         + ["", f"Open the alert: {link}", "", "SCICP platform"])
        rows = "".join(f"<tr><th align='left'>{html.escape(name)}</th><td>{html.escape(str(value))}</td></tr>"
                       for name, value in facts)
        page = (f"<p>A new <b>{html.escape(alert.alert_type_code)}</b> alert was raised on "
                f"<b>{html.escape(alert.vehicle_id)}</b>.</p><table>{rows}</table>"
                f"<p><a href=\"{html.escape(link)}\">Open the alert</a></p><p>SCICP platform</p>")
        message.set_content(text)
        message.add_alternative(f"<html><body>{page}</body></html>", subtype="html")
        return message

    async def send(self, alert: Alert, recipient: str, time_zone: str = "UTC") -> None:
        """Delivers one email; raises MailError when the attempt failed."""
        message = self.build(alert, recipient, time_zone)
        if self.mode == "log":
            log.info("MAIL (log mode) to=%s subject=%r\n%s", recipient, message["Subject"],
                     message.get_body(("plain",)).get_content())
            return
        if self.mode != "smtp":
            raise MailError(f"MAIL_MODE={self.mode} is not supported by C3 (smtp or log)")
        try:
            await asyncio.to_thread(self._smtp, message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailError(f"SMTP {self.smtp_host}:{self.smtp_port}: {exc}") from exc

    def _smtp(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=SMTP_TIMEOUT_S) as smtp:
            smtp.send_message(message)


def describe(exc: BaseException, limit: int = 255) -> Optional[str]:
    """notification.last_error is VARCHAR(255)."""
    return (str(exc) or exc.__class__.__name__)[:limit]
