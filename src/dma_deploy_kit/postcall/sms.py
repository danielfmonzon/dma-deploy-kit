"""SMS sender for the post-call service (booking-link texts).

Follows the alerts.py sink pattern. Sends are gated on explicit consent and a
send-once JSONL ledger so a Retell webhook retry never double-texts a caller.

Twilio Messages API (verified against
https://www.twilio.com/docs/messaging/api/message-resource):
  POST https://api.twilio.com/2010-04-01/Accounts/{AccountSid}/Messages.json
  HTTP Basic auth (Account SID : Auth Token), application/x-www-form-urlencoded,
  form fields To / From / Body in E.164.

NOTE: toll-free Twilio numbers require Toll-Free Verification before carriers
deliver SMS; until then sends are carrier-blocked.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx

from ..config.models import ClientConfig
from .lead import Lead

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LEDGER_PATH = REPO_ROOT / "var" / "sms_ledger.jsonl"
TWILIO_ENV_KEYS = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")


class SmsError(RuntimeError):
    """Raised when an SMS provider call fails."""


@dataclass(frozen=True)
class SmsResult:
    status: str  # "sent" | "debug" | "skipped-duplicate" | "failed"
    to: str
    call_id: str | None = None
    sid: str | None = None
    detail: str | None = None


# --------------------------------------------------------------------------- #
# phone normalization
# --------------------------------------------------------------------------- #
def normalize_us_phone(raw: object) -> str | None:
    """Return a US number as E.164 (+1XXXXXXXXXX), or None if it isn't valid.

    Accepts 10-digit US numbers and 11-digit numbers starting with 1, tolerating
    spaces, dashes, dots, parentheses, and a leading '+1'. Rejects everything else
    (e.g. the eleven-5s "55555555555" from the live test, which is 11 digits not
    starting with 1).
    """
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


# --------------------------------------------------------------------------- #
# message template (engine-owned)
# --------------------------------------------------------------------------- #
def booking_sms_body(business_name: str, booking_url: str) -> str:
    """Friendly one-liner with the business name and booking link."""
    return f"Hi from {business_name}! Book your appointment here: {booking_url}"


# --------------------------------------------------------------------------- #
# send-once ledger
# --------------------------------------------------------------------------- #
class SmsLedger:
    """Append-only JSONL record of SMS sends, keyed by call_id (send-once)."""

    def __init__(self, path: Path = DEFAULT_LEDGER_PATH) -> None:
        self.path = Path(path)

    def contains(self, call_id: str) -> bool:
        if not call_id or not self.path.exists():
            return False
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("call_id") == call_id:
                    return True
            except json.JSONDecodeError:
                continue
        return False

    def record(self, *, call_id: str, to: str, status: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "call_id": call_id,
            "to": to,
            "status": status,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


# --------------------------------------------------------------------------- #
# sinks
# --------------------------------------------------------------------------- #
class SmsSink(Protocol):
    def send(self, to_e164: str, body: str, idempotency_key: str) -> SmsResult: ...


class DebugSms:
    """Record sends in memory (tests / when Twilio env is absent)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, to_e164: str, body: str, idempotency_key: str) -> SmsResult:
        self.sent.append({"to": to_e164, "body": body, "idempotency_key": idempotency_key})
        logger.info("DebugSms to %s (%d chars): %s", to_e164, len(body), body)
        return SmsResult(status="debug", to=to_e164, call_id=idempotency_key)


class TwilioSms:
    """Send SMS via Twilio's REST Messages API using httpx (no Twilio SDK)."""

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self._client = httpx.Client(
            auth=(account_sid, auth_token), transport=transport, timeout=timeout
        )

    @classmethod
    def from_env(cls) -> TwilioSms:
        sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
        token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
        from_number = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
        if not (sid and token and from_number):
            raise SmsError("TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM_NUMBER must all be set.")
        return cls(account_sid=sid, auth_token=token, from_number=from_number)

    def send(self, to_e164: str, body: str, idempotency_key: str) -> SmsResult:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        resp = self._client.post(
            url, data={"To": to_e164, "From": self.from_number, "Body": body}
        )
        if resp.status_code >= 400:
            raise SmsError(f"Twilio POST -> HTTP {resp.status_code}: {resp.text}")
        data = resp.json()
        return SmsResult(
            status="sent",
            to=to_e164,
            call_id=idempotency_key,
            sid=data.get("sid"),
            detail=data.get("status"),
        )

    def close(self) -> None:
        self._client.close()


def twilio_configured() -> bool:
    return all(os.environ.get(k, "").strip() for k in TWILIO_ENV_KEYS)


def default_sms_sink() -> SmsSink:
    """TwilioSms when the env is fully configured, else DebugSms."""
    if twilio_configured():
        try:
            return TwilioSms.from_env()
        except SmsError:
            logger.warning("Twilio env incomplete; using DebugSms")
    return DebugSms()


# --------------------------------------------------------------------------- #
# orchestration: consent gating + send-once
# --------------------------------------------------------------------------- #
def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "y")
    return bool(value)


def phone_field_name(config: ClientConfig) -> str | None:
    """Convention: the caller's phone is the FIRST post_call field whose name
    contains "phone" (e.g. "caller_phone"). Documented and flagged for review."""
    for field in config.post_call:
        if "phone" in field.name.lower():
            return field.name
    return None


def prepare_booking_sms(config: ClientConfig, lead: Lead) -> tuple[str, str] | None:
    """Return (to_e164, body) iff every consent/gating condition holds, else None."""
    booking = config.booking
    if not booking.sms_consent:
        return None
    if not booking.url:
        return None
    if not _is_truthy(lead.fields.get("consent_to_text")):
        return None
    field = phone_field_name(config)
    if field is None:
        return None
    to_e164 = normalize_us_phone(lead.fields.get(field))
    if to_e164 is None:
        return None
    return to_e164, booking_sms_body(config.client.business_name, booking.url)


def send_once(
    sink: SmsSink, ledger: SmsLedger, *, to_e164: str, body: str, call_id: str
) -> SmsResult:
    """Send unless this call_id is already in the ledger; record the outcome."""
    if ledger.contains(call_id):
        logger.info("SMS send-once skip: call_id %s already in ledger", call_id)
        return SmsResult(status="skipped-duplicate", to=to_e164, call_id=call_id)
    result = sink.send(to_e164, body, call_id)
    ledger.record(call_id=call_id, to=to_e164, status=result.status)
    return result


def maybe_send_booking_sms(
    config: ClientConfig, lead: Lead, sink: SmsSink, ledger: SmsLedger
) -> SmsResult | None:
    """Full gate + send-once for one processed lead. Returns None when not sent."""
    prep = prepare_booking_sms(config, lead)
    if prep is None:
        return None
    to_e164, body = prep
    return send_once(sink, ledger, to_e164=to_e164, body=body, call_id=lead.call_id)
