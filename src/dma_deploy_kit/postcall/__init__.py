"""Post-call webhook service: verify Retell webhooks, extract leads, alert."""

from __future__ import annotations

from .alerts import AlertSink, DebugAlert, EmailAlert, default_alert_factory, format_lead_text
from .lead import AgentBinding, AgentRegistry, Lead, parse_lead
from .service import create_app, get_app
from .signature import SignatureCheck, build_signature, check_signature, verify_signature

__all__ = [
    "AgentBinding",
    "AgentRegistry",
    "AlertSink",
    "DebugAlert",
    "EmailAlert",
    "Lead",
    "SignatureCheck",
    "build_signature",
    "check_signature",
    "create_app",
    "default_alert_factory",
    "format_lead_text",
    "get_app",
    "parse_lead",
    "verify_signature",
]
