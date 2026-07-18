"""Alert sinks for post-call leads.

- :class:`DebugAlert` records/logs the lead (used in tests and whenever a client
  has no ``alert_email`` configured).
- :class:`EmailAlert` sends a plain-text lead summary over SMTP (config from env).

``default_alert_factory`` picks EmailAlert when the client configured an
``alert_email`` and SMTP is available, otherwise DebugAlert.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Protocol

from ..config.models import ClientMeta
from .lead import Lead

logger = logging.getLogger(__name__)


def format_lead_text(lead: Lead) -> str:
    """Render a clean plain-text summary of a lead (email body / debug log)."""
    lines = [
        f"New lead — {lead.business_name}",
        f"Language: {lead.language}",
        "",
        "Captured fields:",
    ]
    if lead.fields:
        for name, value in lead.fields.items():
            shown = "(not captured)" if value is None or value == "" else value
            lines.append(f"  - {name}: {shown}")
    else:
        lines.append("  (none configured)")

    def show(value: object) -> object:
        return value if value not in (None, "") else "(unknown)"

    lines += [
        "",
        "Call details:",
        f"  - call_id: {lead.call_id}",
        f"  - agent_id: {lead.agent_id}",
        f"  - from: {show(lead.from_number)}",
        f"  - to: {show(lead.to_number)}",
        f"  - duration_ms: {show(lead.duration_ms)}",
        f"  - start_timestamp: {show(lead.start_timestamp)}",
        f"  - end_timestamp: {show(lead.end_timestamp)}",
        f"  - disconnection_reason: {show(lead.disconnection_reason)}",
    ]
    return "\n".join(lines)


class AlertSink(Protocol):
    def send(self, lead: Lead) -> None: ...


class DebugAlert:
    """Log the lead and retain it in memory (inspectable by tests)."""

    def __init__(self) -> None:
        self.sent: list[Lead] = []

    def send(self, lead: Lead) -> None:
        self.sent.append(lead)
        logger.info("DebugAlert lead for %s:\n%s", lead.business_name, format_lead_text(lead))


class EmailAlert:
    """Send the lead summary as a plain-text email over SMTP."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str | None,
        password: str | None,
        sender: str,
        recipient: str,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender
        self.recipient = recipient

    @classmethod
    def from_env(cls, recipient: str) -> EmailAlert:
        host = os.environ.get("SMTP_HOST", "").strip()
        if not host:
            raise ValueError("SMTP_HOST is not set; cannot build EmailAlert.")
        sender = os.environ.get("SMTP_FROM", "").strip() or os.environ.get("SMTP_USER", "").strip()
        if not sender:
            raise ValueError("SMTP_FROM (or SMTP_USER) is not set; cannot build EmailAlert.")
        return cls(
            host=host,
            port=int(os.environ.get("SMTP_PORT", "587")),
            user=os.environ.get("SMTP_USER") or None,
            password=os.environ.get("SMTP_PASSWORD") or None,
            sender=sender,
            recipient=recipient,
        )

    def build_message(self, lead: Lead) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg["Subject"] = f"New lead — {lead.business_name} ({lead.language})"
        msg.set_content(format_lead_text(lead))
        return msg

    def send(self, lead: Lead) -> None:
        msg = self.build_message(lead)
        with smtplib.SMTP(self.host, self.port) as smtp:
            smtp.starttls()
            if self.user and self.password:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)
        logger.info("EmailAlert sent lead %s to %s", lead.call_id, self.recipient)


def default_alert_factory(client: ClientMeta) -> AlertSink:
    """EmailAlert when alert_email + SMTP are configured; else DebugAlert."""
    if client.alert_email and os.environ.get("SMTP_HOST", "").strip():
        try:
            return EmailAlert.from_env(recipient=client.alert_email)
        except ValueError:
            logger.warning("alert_email set but SMTP config incomplete; using DebugAlert")
    return DebugAlert()
