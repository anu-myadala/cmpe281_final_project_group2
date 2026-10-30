"""MailSender: invitation and password-reset emails.

MAIL_MODE=log writes the message to the log (tests, developer mode),
smtp sends through MailHog or any SMTP relay, ses uses Amazon SES.
A failure is logged and never raised: the API still answers 201 / 202.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from ..config import Settings

log = logging.getLogger("scicp.mail")


class MailSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sent: list = []  # kept for the tests and the log mode

    async def send_invitation(self, email: str, name: str, temp_password: str) -> None:
        subject = "Your SCICP account"
        body = (f"Hello {name},\n\nAn account was created for you on the Smart Car Intelligent Cloud Platform.\n"
                f"Sign in with this temporary password and choose a new one at first login:\n\n"
                f"    {temp_password}\n\nSCICP platform")
        await self._send(email, subject, body)

    async def send_reset(self, email: str, name: str, temp_password: str) -> None:
        subject = "SCICP password reset"
        body = (f"Hello {name},\n\nYour password was reset by the platform administrator.\n"
                f"Sign in with this temporary password and choose a new one:\n\n    {temp_password}\n\nSCICP platform")
        await self._send(email, subject, body)

    async def _send(self, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})
        mode = self.settings.mail_mode.lower()
        try:
            if mode == "smtp":
                await asyncio.to_thread(self._smtp, to, subject, body)
            elif mode == "ses":
                await asyncio.to_thread(self._ses, to, subject, body)
            else:
                log.info("MAIL (log mode) to=%s subject=%r\n%s", to, subject, body)
        except Exception as exc:  # noqa: BLE001 - mail failures are logged, not raised
            log.error("Mail to %s failed: %s", to, exc)

    def _smtp(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.mail_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=5) as smtp:
            smtp.send_message(message)

    def _ses(self, to: str, subject: str, body: str) -> None:
        import boto3
        ses = boto3.client("ses", region_name=self.settings.s3_region)
        ses.send_email(Source=self.settings.mail_from, Destination={"ToAddresses": [to]},
                       Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}})
