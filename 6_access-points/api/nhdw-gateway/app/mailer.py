"""Application SMTP mailer (external SMTP relay).

All application email goes through here: disease-surveillance alert
notifications, access-grant and password-reset messages, submission decisions.
Sending is best-effort: a mail failure never blocks the in-app notification,
which is the source of truth (topbar bell, "My alerts" panel). The blocking
smtplib send runs in a thread executor so the async evaluator does not stall
the event loop.

SMTP_* default to Gmail (smtp.gmail.com:587, STARTTLS); SMTP_USER and SMTP_PASS
come from the smtp-secrets Secret. Without them a send fails and is logged; it
is never silently discarded.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from .settings import settings

log = logging.getLogger("gateway.mailer")


def _send_blocking(to_addr: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as s:
        # Authenticated relay: STARTTLS on 587 with an app-password login.
        if settings.SMTP_TLS:
            s.starttls()
        if settings.SMTP_USER:
            s.login(settings.SMTP_USER, settings.SMTP_PASS)
        s.send_message(msg)


async def send(to_addr: str, subject: str, body: str) -> bool:
    """Send one text email. Returns True on success, False on any failure
    (logged, never raised) or when SMTP or the recipient is not configured."""
    if not settings.SMTP_HOST or not to_addr:
        return False
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _send_blocking, to_addr, subject, body)
        return True
    except Exception as e:  # noqa: BLE001 (best-effort; never block the alert)
        log.warning("alert email to %s failed: %s", to_addr, e)
        return False
