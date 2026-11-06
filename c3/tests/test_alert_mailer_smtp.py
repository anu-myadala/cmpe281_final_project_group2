"""The email over real SMTP: AlertMailer (smtplib in a worker thread, as C1's MailSender) talks
to a tiny SMTP server running in this test's event loop on 127.0.0.1. C1 pins no aiosmtpd, so
the server is ~30 lines of asyncio. The bytes it receives are parsed back to prove the message
is valid: headers, a multipart/alternative with a text and an HTML part, and the link."""
from __future__ import annotations

import asyncio
import email
import logging
from datetime import datetime
from email import policy

import pytest

from c3_service.config import Settings
from c3_service.models import Alert
from c3_service.services.alert_mailer import AlertMailer, MailError


class TinySmtpServer:
    """Just enough RFC 5321 for smtplib: greeting, EHLO/HELO, MAIL, RCPT, DATA, QUIT."""

    def __init__(self) -> None:
        self.messages = []  # (mail_from, [rcpt], raw bytes)
        self.server = None
        self.port = None

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._session, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        def reply(line: str) -> None:
            writer.write(line.encode() + b"\r\n")

        reply("220 tiny-smtp ready")
        mail_from, rcpts = None, []
        while True:
            line = (await reader.readline()).decode().rstrip("\r\n")
            verb = line.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                reply("250 tiny-smtp")
            elif verb == "MAIL":
                mail_from, rcpts = line[10:].strip("<>"), []
                reply("250 OK")
            elif verb == "RCPT":
                rcpts.append(line[8:].strip("<>"))
                reply("250 OK")
            elif verb == "DATA":
                reply("354 end with <CRLF>.<CRLF>")
                data = await reader.readuntil(b"\r\n.\r\n")
                self.messages.append((mail_from, rcpts, data[:-5].replace(b"\r\n..", b"\r\n.")))
                reply("250 queued")
            elif verb in ("QUIT", ""):
                reply("221 bye")
                await writer.drain()
                writer.close()
                return
            else:
                reply("250 OK")
            await writer.drain()


def _alert() -> Alert:
    return Alert(alert_id=30001, tenant_id=1, vehicle_id="AV-01", alert_type_code="ACCIDENT", top_class_id=2,
                 severity="CRITICAL", confidence=0.91, created_at=datetime(2026, 11, 7, 7, 10, 5))


async def test_email_over_smtp_is_a_valid_message():
    async with TinySmtpServer() as server:
        mailer = AlertMailer(Settings(_env_file=None, mail_mode="smtp", smtp_host="127.0.0.1", smtp_port=server.port,
                                      mail_from="no-reply@scicp.example", public_base_url="http://localhost:8080"))
        await mailer.send(_alert(), "anh@example.com", "America/Los_Angeles")
    ((mail_from, rcpts, raw),) = server.messages
    assert (mail_from, rcpts) == ("no-reply@scicp.example", ["anh@example.com"])
    message = email.message_from_bytes(raw, policy=policy.default)
    assert message["Subject"] == "[SCICP] ACCIDENT alert on AV-01: crash (CRITICAL)"
    assert message["From"] == "no-reply@scicp.example" and message["To"] == "anh@example.com"
    assert message.get_content_type() == "multipart/alternative"
    assert [p.get_content_type() for p in message.iter_parts()] == ["text/plain", "text/html"]
    assert "Open the alert: http://localhost:8080/alerts/30001" in message.get_body(("plain",)).get_content()
    assert 'href="http://localhost:8080/alerts/30001"' in message.get_body(("html",)).get_content()


async def test_smtp_server_down_is_a_mail_error():
    async with TinySmtpServer() as server:
        port = server.port  # the server is closed after this block
    mailer = AlertMailer(Settings(_env_file=None, mail_mode="smtp", smtp_host="127.0.0.1", smtp_port=port))
    with pytest.raises(MailError, match="SMTP 127.0.0.1"):
        await mailer.send(_alert(), "anh@example.com")


async def test_log_mode_writes_the_email_to_the_log(caplog):
    mailer = AlertMailer(Settings(_env_file=None, mail_mode="log"))
    with caplog.at_level(logging.INFO, logger="scicp.c3.mail"):
        await mailer.send(_alert(), "anh@example.com", "America/Los_Angeles")
    assert "MAIL (log mode) to=anh@example.com" in caplog.text and "/alerts/30001" in caplog.text


async def test_ses_mode_fails_clearly_in_c3():
    mailer = AlertMailer(Settings(_env_file=None, mail_mode="ses"))
    with pytest.raises(MailError, match="MAIL_MODE=ses is not supported by C3"):
        await mailer.send(_alert(), "anh@example.com")
