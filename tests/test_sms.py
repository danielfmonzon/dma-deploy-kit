"""Tests for the SMS sender: normalization, gating, ledger, Twilio format, flow."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from dma_deploy_kit.config import load_client_config
from dma_deploy_kit.postcall import (
    AgentBinding,
    AgentRegistry,
    DebugAlert,
    DebugSms,
    Lead,
    SmsLedger,
    TwilioSms,
    booking_sms_body,
    build_signature,
    create_app,
    maybe_send_booking_sms,
    normalize_us_phone,
    phone_field_name,
    prepare_booking_sms,
    send_once,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "config" / "client.example.yaml"
KEY = "whk_test_key"
AGENT_ID = "agent_test_en"


@pytest.fixture
def acme():
    return load_client_config(EXAMPLE_PATH)


def _lead(fields: dict, call_id: str = "call_1") -> Lead:
    return Lead(
        slug="acme-wellness",
        business_name="Acme Wellness",
        language="en-US",
        call_id=call_id,
        agent_id=AGENT_ID,
        fields=fields,
    )


# --------------------------------------------------------------------------- #
# phone normalization
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5551234567", "+15551234567"),           # 10-digit
        ("15551234567", "+15551234567"),          # 11-digit leading 1
        ("+15551234567", "+15551234567"),         # already E.164
        ("(555) 123-4567", "+15551234567"),       # parens + spaces
        ("555-123-4567", "+15551234567"),         # dashes
        ("555.123.4567", "+15551234567"),         # dots
        ("+1 (555) 123-4567", "+15551234567"),    # +1 with separators
        (" 1-555-123-4567 ", "+15551234567"),     # whitespace + leading 1
        ("55555555555", None),                    # eleven 5s (live-test case)
        ("2025550123", "+12025550123"),           # generic 10-digit
        ("123", None),                            # too short
        ("+445551234567", None),                  # non-US / 12 digits
        ("", None),
        (None, None),
        ("abcdefghij", None),
    ],
)
def test_normalize_us_phone(raw, expected):
    assert normalize_us_phone(raw) == expected


# --------------------------------------------------------------------------- #
# template
# --------------------------------------------------------------------------- #
def test_booking_sms_body_under_160_for_acme(acme):
    body = booking_sms_body(acme.client.business_name, acme.booking.url)
    assert acme.client.business_name in body
    assert acme.booking.url in body
    assert len(body) < 160, f"body is {len(body)} chars"


# --------------------------------------------------------------------------- #
# phone-field convention
# --------------------------------------------------------------------------- #
def test_phone_field_convention(acme):
    # first post_call field whose name contains "phone"
    assert phone_field_name(acme) == "caller_phone"


# --------------------------------------------------------------------------- #
# gating matrix
# --------------------------------------------------------------------------- #
def _happy_fields():
    return {"consent_to_text": True, "caller_phone": "555-123-4567"}


def test_gate_happy_path(acme):
    prep = prepare_booking_sms(acme, _lead(_happy_fields()))
    assert prep is not None
    to, body = prep
    assert to == "+15551234567"
    assert body == booking_sms_body("Acme Wellness", acme.booking.url)


def test_gate_sms_consent_false(acme):
    cfg = acme.model_copy(
        update={"booking": acme.booking.model_copy(update={"sms_consent": False})}
    )
    assert prepare_booking_sms(cfg, _lead(_happy_fields())) is None


def test_gate_no_booking_url(acme):
    cfg = acme.model_copy(update={"booking": acme.booking.model_copy(update={"url": None})})
    assert prepare_booking_sms(cfg, _lead(_happy_fields())) is None


def test_gate_consent_to_text_falsy(acme):
    fields = {"consent_to_text": False, "caller_phone": "555-123-4567"}
    assert prepare_booking_sms(acme, _lead(fields)) is None
    # absent consent field -> falsy
    assert prepare_booking_sms(acme, _lead({"caller_phone": "555-123-4567"})) is None


def test_gate_phone_not_normalizable(acme):
    fields = {"consent_to_text": True, "caller_phone": "55555555555"}  # eleven 5s
    assert prepare_booking_sms(acme, _lead(fields)) is None


def test_gate_no_phone_field(tmp_path):
    import yaml
    data = {
        "client": {"slug": "np", "business_name": "NoPhone", "vertical": "salon",
                   "timezone": "America/New_York"},
        "languages": [{"code": "en-US", "voice_id": "retell-Tamsin", "greeting": "Hi."}],
        "facts": {"description": "x"},
        "booking": {"url": "https://np.example.com/book", "sms_consent": True},
        "escalation": {"contact_name": "Sam"},
        "post_call": [{"name": "consent_to_text", "type": "boolean", "description": "c"}],
    }
    p = tmp_path / "np.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    cfg = load_client_config(p)
    assert phone_field_name(cfg) is None
    assert prepare_booking_sms(cfg, _lead({"consent_to_text": True})) is None


# --------------------------------------------------------------------------- #
# ledger + send-once
# --------------------------------------------------------------------------- #
def test_ledger_round_trip(tmp_path):
    ledger = SmsLedger(tmp_path / "sms.jsonl")
    assert ledger.contains("c1") is False
    ledger.record(call_id="c1", to="+15551234567", status="sent")
    assert ledger.contains("c1") is True
    assert ledger.contains("c2") is False
    entries = [json.loads(x) for x in (tmp_path / "sms.jsonl").read_text().splitlines()]
    assert entries[0]["call_id"] == "c1"
    assert entries[0]["to"] == "+15551234567"
    assert "timestamp" in entries[0]


def test_send_once_skips_duplicate(tmp_path):
    ledger = SmsLedger(tmp_path / "sms.jsonl")
    sink = DebugSms()
    r1 = send_once(sink, ledger, to_e164="+15551234567", body="hi", call_id="dup")
    assert r1.status == "debug"
    assert len(sink.sent) == 1
    r2 = send_once(sink, ledger, to_e164="+15551234567", body="hi", call_id="dup")
    assert r2.status == "skipped-duplicate"
    assert len(sink.sent) == 1  # not sent again


# --------------------------------------------------------------------------- #
# Twilio request format (mocked transport)
# --------------------------------------------------------------------------- #
def test_twilio_request_format():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["ctype"] = request.headers.get("content-type")
        captured["form"] = parse_qs(request.content.decode())
        return httpx.Response(201, json={"sid": "SM123", "status": "queued"})

    sms = TwilioSms(
        account_sid="AC_test", auth_token="tok_test", from_number="+15550000000",
        transport=httpx.MockTransport(handler),
    )
    result = sms.send("+15551234567", "Book here: https://x/book", "call_abc")

    assert captured["method"] == "POST"
    assert captured["url"] == (
        "https://api.twilio.com/2010-04-01/Accounts/AC_test/Messages.json"
    )
    assert captured["auth"].startswith("Basic ")
    decoded = base64.b64decode(captured["auth"].split(" ", 1)[1]).decode()
    assert decoded == "AC_test:tok_test"
    assert "x-www-form-urlencoded" in captured["ctype"]
    assert captured["form"]["To"] == ["+15551234567"]
    assert captured["form"]["From"] == ["+15550000000"]
    assert captured["form"]["Body"] == ["Book here: https://x/book"]
    assert result.status == "sent"
    assert result.sid == "SM123"


# --------------------------------------------------------------------------- #
# full webhook flow via TestClient
# --------------------------------------------------------------------------- #
def _signed_post(client, payload, key=KEY):
    raw = json.dumps(payload).encode("utf-8")
    sig = build_signature(raw, key, int(time.time() * 1000))
    return client.post("/webhook/retell", content=raw, headers={"x-retell-signature": sig})


def _payload(consent=True, phone="555-123-4567", call_id="call_flow_1"):
    return {
        "event": "call_analyzed",
        "call": {
            "call_id": call_id,
            "agent_id": AGENT_ID,
            "call_analysis": {
                "custom_analysis_data": {
                    "caller_name": "Jane",
                    "caller_phone": phone,
                    "consent_to_text": consent,
                },
            },
        },
    }


def test_full_flow_sends_debug_sms(acme, tmp_path):
    reg = AgentRegistry({AGENT_ID: AgentBinding("acme-wellness", "en-US", acme)})
    debug_sms = DebugSms()
    ledger = SmsLedger(tmp_path / "sms.jsonl")
    app = create_app(
        webhook_key=KEY, registry=reg, sms_sink=debug_sms, sms_ledger=ledger,
        alert_factory=lambda m: DebugAlert(),
    )
    client = TestClient(app)

    resp = _signed_post(client, _payload())
    assert resp.status_code == 200
    assert len(debug_sms.sent) == 1
    sent = debug_sms.sent[0]
    assert sent["to"] == "+15551234567"
    assert "Acme Wellness" in sent["body"]
    assert ledger.contains("call_flow_1")

    # retry same call_id -> no second send (idempotent through the flow)
    resp2 = _signed_post(client, _payload())
    assert resp2.status_code == 200
    assert len(debug_sms.sent) == 1


def test_full_flow_no_send_without_consent(acme, tmp_path):
    reg = AgentRegistry({AGENT_ID: AgentBinding("acme-wellness", "en-US", acme)})
    debug_sms = DebugSms()
    app = create_app(
        webhook_key=KEY, registry=reg, sms_sink=debug_sms,
        sms_ledger=SmsLedger(tmp_path / "sms.jsonl"),
    )
    client = TestClient(app)
    resp = _signed_post(client, _payload(consent=False))
    assert resp.status_code == 200
    assert debug_sms.sent == []


def test_maybe_send_returns_none_when_gated(acme, tmp_path):
    ledger = SmsLedger(tmp_path / "sms.jsonl")
    lead = _lead({"consent_to_text": False, "caller_phone": "5551234567"})
    assert maybe_send_booking_sms(acme, lead, DebugSms(), ledger) is None
