"""Tests for the post-call webhook service."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dma_deploy_kit.config import load_client_config
from dma_deploy_kit.postcall import (
    AgentBinding,
    AgentRegistry,
    DebugAlert,
    EmailAlert,
    build_signature,
    check_signature,
    create_app,
    default_alert_factory,
    format_lead_text,
    parse_lead,
    verify_signature,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "config" / "client.example.yaml"
KEY = "whk_test_key"
AGENT_ID = "agent_test_en"


@pytest.fixture
def acme():
    return load_client_config(EXAMPLE_PATH)


@pytest.fixture
def registry(acme):
    binding = AgentBinding(slug="acme-wellness", language="en-US", config=acme)
    return AgentRegistry({AGENT_ID: binding})


def _call_analyzed_payload(agent_id: str = AGENT_ID) -> dict:
    return {
        "event": "call_analyzed",
        "call": {
            "call_id": "call_abc123",
            "agent_id": agent_id,
            "from_number": "+15550001111",
            "to_number": "+15550002222",
            "start_timestamp": 1_700_000_000_000,
            "end_timestamp": 1_700_000_180_000,
            "disconnection_reason": "user_hangup",
            "call_analysis": {
                "call_summary": "Caller asked about facials.",
                "custom_analysis_data": {
                    "caller_name": "Jane Roe",
                    "caller_phone": "+15550001111",
                    "appointment_urgency": "this_week",
                    "consent_to_text": True,
                },
            },
        },
    }


def _post(client: TestClient, payload: dict, *, key: str = KEY, now_ms: int | None = None):
    raw = json.dumps(payload).encode("utf-8")
    ts = now_ms if now_ms is not None else int(time.time() * 1000)
    sig = build_signature(raw, key, ts)
    return client.post("/webhook/retell", content=raw, headers={"x-retell-signature": sig})


# --------------------------------------------------------------------------- #
# signature
# --------------------------------------------------------------------------- #
def test_signature_round_trip():
    raw = b'{"event":"call_analyzed"}'
    ts = int(time.time() * 1000)
    sig = build_signature(raw, KEY, ts)
    assert verify_signature(raw, sig, KEY)


def test_signature_rejects_wrong_key():
    raw = b"body"
    ts = int(time.time() * 1000)
    sig = build_signature(raw, KEY, ts)
    assert not verify_signature(raw, sig, "other_key")


def test_signature_rejects_tampered_body():
    ts = int(time.time() * 1000)
    sig = build_signature(b"original", KEY, ts)
    assert not verify_signature(b"tampered", sig, KEY)


def test_signature_rejects_absent_and_malformed():
    raw = b"body"
    assert not verify_signature(raw, None, KEY)
    assert not verify_signature(raw, "", KEY)
    assert not verify_signature(raw, "garbage", KEY)


def test_check_signature_diagnostics():
    raw = b"body"
    now = 2_000_000_000_000
    # absent header
    c = check_signature(raw, None, KEY, now_ms=now)
    assert c.header_present is False and c.valid is False
    # valid
    sig = build_signature(raw, KEY, now)
    c = check_signature(raw, sig, KEY, now_ms=now)
    assert c.valid and c.digest_match and c.skew_ms == 0 and c.parsed_timestamp == now
    # stale but digest matches -> digest_match True, valid False, skew reported
    old = now - 10 * 60 * 1000
    c = check_signature(raw, build_signature(raw, KEY, old), KEY, now_ms=now)
    assert c.digest_match is True and c.valid is False and c.skew_ms == 10 * 60 * 1000
    # wrong key -> digest_match False
    c = check_signature(raw, build_signature(raw, "other", now), KEY, now_ms=now)
    assert c.digest_match is False and c.valid is False


def test_signature_rejects_stale_timestamp():
    raw = b"body"
    now = 2_000_000_000_000
    old = now - 10 * 60 * 1000  # 10 minutes old
    sig = build_signature(raw, KEY, old)
    assert not verify_signature(raw, sig, KEY, now_ms=now)  # default 5-min tolerance
    # but fine within a wide tolerance
    assert verify_signature(raw, sig, KEY, now_ms=now, tolerance_ms=30 * 60 * 1000)


# --------------------------------------------------------------------------- #
# fail-closed startup
# --------------------------------------------------------------------------- #
def test_create_app_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv("RETELL_WEBHOOK_KEY", raising=False)
    with pytest.raises(RuntimeError):
        create_app()


# --------------------------------------------------------------------------- #
# webhook endpoint
# --------------------------------------------------------------------------- #
def test_webhook_rejects_bad_signature(registry):
    app = create_app(webhook_key=KEY, registry=registry, alert_factory=lambda m: DebugAlert())
    client = TestClient(app)
    raw = json.dumps(_call_analyzed_payload()).encode("utf-8")
    # wrong signature
    resp = client.post("/webhook/retell", content=raw, headers={"x-retell-signature": "v=1,d=bad"})
    assert resp.status_code == 401
    # absent signature
    resp2 = client.post("/webhook/retell", content=raw)
    assert resp2.status_code == 401


def test_webhook_processes_call_analyzed(registry):
    debug = DebugAlert()
    app = create_app(webhook_key=KEY, registry=registry, alert_factory=lambda m: debug)
    client = TestClient(app)
    resp = _post(client, _call_analyzed_payload())
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"
    assert len(debug.sent) == 1
    lead = debug.sent[0]
    assert lead.business_name == "Acme Wellness"
    assert lead.language == "en-US"
    assert lead.call_id == "call_abc123"
    assert lead.agent_id == AGENT_ID
    assert lead.from_number == "+15550001111"
    assert lead.duration_ms == 180_000  # end - start
    # all 8 configured fields present; captured ones carry values
    assert set(lead.fields) == {f.name for f in registry.resolve(AGENT_ID).config.post_call}
    assert lead.fields["caller_name"] == "Jane Roe"
    assert lead.fields["appointment_urgency"] == "this_week"
    assert lead.fields["is_returning_client"] is None  # configured but not captured


def test_webhook_ignores_other_events(registry):
    debug = DebugAlert()
    app = create_app(webhook_key=KEY, registry=registry, alert_factory=lambda m: debug)
    client = TestClient(app)
    resp = _post(client, {"event": "call_started", "call": {"agent_id": AGENT_ID}})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert debug.sent == []


def test_webhook_unmanaged_agent_acknowledged(registry):
    debug = DebugAlert()
    app = create_app(webhook_key=KEY, registry=registry, alert_factory=lambda m: debug)
    client = TestClient(app)
    resp = _post(client, _call_analyzed_payload(agent_id="agent_not_ours"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "unmanaged"
    assert debug.sent == []  # no alert for agents we don't manage


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
def test_registry_scans_lockfiles(tmp_path, acme):
    clients = tmp_path / "clients"
    clients.mkdir()
    # a valid client yaml + its lockfile
    (clients / "acme-wellness.yaml").write_text(
        EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (clients / "acme-wellness.lock.json").write_text(
        json.dumps(
            {
                "en-US": {"agent_id": "agent_en", "llm_id": "llm_en"},
                "es-419": {"agent_id": "agent_es", "llm_id": "llm_es"},
            }
        ),
        encoding="utf-8",
    )
    reg = AgentRegistry.from_clients_dir(clients)
    assert len(reg) == 2
    assert reg.resolve("agent_en").language == "en-US"
    assert reg.resolve("agent_es").language == "es-419"
    assert reg.resolve("nope") is None


def test_registry_ignores_lock_without_config(tmp_path):
    clients = tmp_path / "clients"
    clients.mkdir()
    (clients / "ghost.lock.json").write_text(
        json.dumps({"en-US": {"agent_id": "agent_ghost"}}), encoding="utf-8"
    )
    reg = AgentRegistry.from_clients_dir(clients)  # no ghost.yaml
    assert reg.resolve("agent_ghost") is None


# --------------------------------------------------------------------------- #
# lead parsing
# --------------------------------------------------------------------------- #
def test_parse_lead_fields_and_metadata(acme):
    binding = AgentBinding(slug="acme-wellness", language="es-419", config=acme)
    lead = parse_lead(_call_analyzed_payload(), binding)
    assert lead.language == "es-419"
    assert [k for k in lead.fields] == [f.name for f in acme.post_call]
    assert lead.duration_ms == 180_000
    assert lead.disconnection_reason == "user_hangup"


# --------------------------------------------------------------------------- #
# alerts
# --------------------------------------------------------------------------- #
def test_format_lead_text_contains_summary(acme):
    binding = AgentBinding(slug="acme-wellness", language="en-US", config=acme)
    lead = parse_lead(_call_analyzed_payload(), binding)
    text = format_lead_text(lead)
    assert "Acme Wellness" in text
    assert "en-US" in text
    assert "caller_name: Jane Roe" in text
    assert "call_abc123" in text
    assert "+15550001111" in text


def test_email_alert_build_message(acme):
    binding = AgentBinding(slug="acme-wellness", language="en-US", config=acme)
    lead = parse_lead(_call_analyzed_payload(), binding)
    alert = EmailAlert(
        host="smtp.example.com", port=587, user="u", password="p",
        sender="bot@example.com", recipient="leads@example.com",
    )
    msg = alert.build_message(lead)
    assert msg["To"] == "leads@example.com"
    assert "Acme Wellness" in msg["Subject"]
    assert "caller_name: Jane Roe" in msg.get_content()


def test_email_alert_send_uses_smtp(acme, monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    binding = AgentBinding(slug="acme-wellness", language="en-US", config=acme)
    lead = parse_lead(_call_analyzed_payload(), binding)
    EmailAlert(
        host="smtp.example.com", port=25, user="u", password="p",
        sender="bot@example.com", recipient="leads@example.com",
    ).send(lead)
    assert sent["host"] == "smtp.example.com"
    assert sent["starttls"] is True
    assert sent["login"] == ("u", "p")
    assert sent["msg"]["To"] == "leads@example.com"


def test_default_alert_factory_selects_sink(acme, monkeypatch):
    # alert_email unset -> DebugAlert
    meta_no_email = acme.client.model_copy(update={"alert_email": None})
    assert isinstance(default_alert_factory(meta_no_email), DebugAlert)

    # alert_email set + SMTP configured -> EmailAlert
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "bot@example.com")
    meta_email = acme.client.model_copy(update={"alert_email": "leads@example.com"})
    assert isinstance(default_alert_factory(meta_email), EmailAlert)

    # alert_email set but SMTP absent -> DebugAlert fallback
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert isinstance(default_alert_factory(meta_email), DebugAlert)
