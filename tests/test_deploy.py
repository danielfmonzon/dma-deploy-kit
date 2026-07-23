"""Tests for the deploy engine (mocked httpx transport — no live calls)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from dma_deploy_kit.agent import constants
from dma_deploy_kit.agent.deploy import (
    DeployError,
    RetellClient,
    build_desired_state,
    plan,
    read_lockfile,
    write_lockfile,
)
from dma_deploy_kit.config import load_client_config

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "config" / "client.example.yaml"


@pytest.fixture
def acme():
    return load_client_config(EXAMPLE_PATH)


def _single_lang_config(tmp_path: Path):
    data = {
        "client": {
            "slug": "solo",
            "business_name": "Solo Co",
            "vertical": "salon",
            "timezone": "America/New_York",
        },
        "languages": [
            {"code": "en-US", "voice_id": "retell-Tamsin", "greeting": "Hi there."}
        ],
        "facts": {"description": "A solo business."},
        "escalation": {"contact_name": "Sam"},
        "post_call": [
            {"name": "caller_name", "type": "string", "description": "Caller name."}
        ],
    }
    p = tmp_path / "solo.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return load_client_config(p)


def _load_cli():
    import sys
    scripts_dir = str(REPO_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import deploy_client  # noqa: PLC0415

    return deploy_client


# --------------------------------------------------------------------------- #
# sms_consent warning (CLI)
# --------------------------------------------------------------------------- #
def test_sms_consent_warning_appears_for_acme(acme, tmp_path):
    cli = _load_cli()
    assert acme.booking.sms_consent is True
    result = plan(acme, client=None, lockfile=tmp_path / "acme.lock.json")
    out = cli.format_plan(result, acme)
    assert "sms_consent is TRUE" in out
    assert "will NOT be sent" in out


def test_no_sms_consent_warning_when_false(tmp_path):
    cli = _load_cli()
    solo = _single_lang_config(tmp_path)  # no booking block -> sms_consent False
    assert solo.booking.sms_consent is False
    result = plan(solo, client=None, lockfile=tmp_path / "solo.lock.json")
    out = cli.format_plan(result, solo)
    assert "WARNING: booking.sms_consent" not in out


def test_sms_warning_variant_without_twilio(acme, tmp_path, monkeypatch):
    cli = _load_cli()
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    out = cli.format_plan(plan(acme, client=None, lockfile=tmp_path / "a.lock.json"), acme)
    assert "no Twilio credentials are set" in out
    assert "will NOT be sent" in out


def test_sms_warning_variant_with_twilio(acme, tmp_path, monkeypatch):
    cli = _load_cli()
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_x")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")
    out = cli.format_plan(plan(acme, client=None, lockfile=tmp_path / "a.lock.json"), acme)
    assert "Twilio SMS is configured" in out
    assert "will NOT be sent" not in out


# --------------------------------------------------------------------------- #
# desired state
# --------------------------------------------------------------------------- #
def test_desired_state_per_language(acme):
    states = build_desired_state(acme)
    assert [s["code"] for s in states] == ["en-US", "es-419"]

    en, es = states
    # correct voice per language (each pulled from its own language profile)
    assert en["agent"]["voice_id"] == acme.languages[0].voice_id == "retell-Tamsin"
    assert es["agent"]["voice_id"] == acme.languages[1].voice_id
    assert en["agent"]["voice_id"] != es["agent"]["voice_id"]
    # agent_name convention
    assert en["agent_name"] == "Acme Wellness — en-US"
    # compiled prompt embedded, and it is the per-language prompt
    assert en["llm"]["general_prompt"].startswith("# IDENTITY")
    assert "Ava" in en["llm"]["general_prompt"]
    assert en["llm"]["begin_message"] == acme.languages[0].greeting
    # engine constants applied
    assert en["agent"]["allow_user_dtmf"] is constants.AGENT_DEFAULTS["allow_user_dtmf"]
    assert en["agent"]["interruption_sensitivity"] == 0.9
    assert en["llm"]["model"] == "claude-4.6-sonnet"
    assert [t["name"] for t in en["llm"]["general_tools"]] == ["end_call"]


def test_no_webhook_url_when_base_unset(acme, monkeypatch):
    monkeypatch.delenv("WEBHOOK_BASE_URL", raising=False)
    for st in build_desired_state(acme):
        assert "webhook_url" not in st["agent"]
        assert "webhook_url" not in st["llm"]


def test_webhook_url_from_env(acme, monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://tunnel.example.com/")
    for st in build_desired_state(acme):
        # trailing slash on the base is normalized; single /webhook/retell suffix
        assert st["agent"]["webhook_url"] == "https://tunnel.example.com/webhook/retell"
        assert "webhook_url" not in st["llm"]


def test_plan_output_reports_real_webhook_url(acme, tmp_path, monkeypatch):
    """A CREATE plan must print the webhook_url it will actually send."""
    cli = _load_cli()
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://tunnel.example.com")
    result = plan(acme, client=None, lockfile=tmp_path / "acme.lock.json")
    out = cli.format_plan(result, acme)
    assert "webhook_url: https://tunnel.example.com/webhook/retell" in out
    assert "(none" not in out


def test_plan_output_reports_absent_webhook_url(acme, tmp_path, monkeypatch):
    """With no WEBHOOK_BASE_URL the plan says so, and sends no webhook_url."""
    cli = _load_cli()
    monkeypatch.delenv("WEBHOOK_BASE_URL", raising=False)
    result = plan(acme, client=None, lockfile=tmp_path / "acme.lock.json")
    out = cli.format_plan(result, acme)
    assert "webhook_url: (none — set WEBHOOK_BASE_URL" in out
    for item in result["items"]:
        assert "webhook_url" not in item["agent"]


def test_post_call_analysis_data_mapped(acme):
    st = build_desired_state(acme)[0]
    pcad = st["agent"]["post_call_analysis_data"]
    # all 8 Acme fields present, in order, by name
    assert [d["name"] for d in pcad] == [f.name for f in acme.post_call]
    assert len(pcad) == 8
    # Retell shape: every item has exactly type/name/description (+choices for enum)
    for item in pcad:
        assert set(item) <= {"type", "name", "description", "choices"}
        assert {"type", "name", "description"} <= set(item)
        assert item["type"] in {"string", "boolean", "enum", "number"}
        assert "source" not in item  # kit-side concept, not sent to Retell
    # exactly one enum field, and it carries a non-empty choices list
    enums = [d for d in pcad if d["type"] == "enum"]
    assert len(enums) == 1
    assert enums[0]["name"] == "appointment_urgency"
    assert enums[0]["choices"] == ["urgent", "this_week", "flexible"]
    # boolean/number/string fields carry no choices key
    for item in pcad:
        if item["type"] != "enum":
            assert "choices" not in item


def test_post_call_analysis_model_from_constants(acme):
    model = build_desired_state(acme)[0]["agent"]["post_call_analysis_model"]
    assert model == constants.AGENT_DEFAULTS["post_call_analysis_model"]
    assert model == "gpt-4.1"


def test_multi_language_sets_language_list(acme):
    states = build_desired_state(acme)
    for st in states:
        assert st["agent"]["language"] == ["en-US", "es-419"]


def test_single_language_uses_string(tmp_path):
    config = _single_lang_config(tmp_path)
    st = build_desired_state(config)[0]
    assert st["agent"]["language"] == "en-US"


# --------------------------------------------------------------------------- #
# lockfile
# --------------------------------------------------------------------------- #
def test_lockfile_round_trip(tmp_path):
    path = tmp_path / "x.lock.json"
    data = {"en-US": {"agent_id": "agent_1", "llm_id": "llm_1"}}
    write_lockfile(path, data)
    assert read_lockfile(path) == data
    assert read_lockfile(tmp_path / "missing.lock.json") == {}


# --------------------------------------------------------------------------- #
# plan: CREATE (no lockfile) — offline, no client needed
# --------------------------------------------------------------------------- #
def test_first_deploy_plans_create(acme, tmp_path):
    result = plan(acme, client=None, lockfile=tmp_path / "acme.lock.json")
    assert [i["action"] for i in result["items"]] == ["CREATE", "CREATE"]
    assert result["items"][0]["agent_name"] == "Acme Wellness — en-US"
    # CREATE items carry the full desired payloads
    assert "agent" in result["items"][0] and "llm" in result["items"][0]


# --------------------------------------------------------------------------- #
# plan: UPDATE / NOOP (lockfile present) — mocked live state
# --------------------------------------------------------------------------- #
def _mock_client(acme, live_overrides=None):
    """Build a RetellClient whose GETs return desired state (optionally mutated)."""
    states = {s["code"]: s for s in build_desired_state(acme)}
    # lockfile maps code -> ids; agent_id/llm_id encode the code for routing
    id_to_code = {}
    for code in states:
        id_to_code[f"agent::{code}"] = code
        id_to_code[f"llm::{code}"] = code
    overrides = live_overrides or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/get-agent/"):
            code = id_to_code[path.rsplit("/", 1)[1]]
            live = dict(states[code]["agent"])
            live.update(overrides.get(("agent", code), {}))
            return httpx.Response(200, json=live)
        if path.startswith("/get-retell-llm/"):
            code = id_to_code[path.rsplit("/", 1)[1]]
            live = dict(states[code]["llm"])
            live.update(overrides.get(("llm", code), {}))
            return httpx.Response(200, json=live)
        return httpx.Response(404, json={"error": f"unexpected {path}"})

    return RetellClient(transport=httpx.MockTransport(handler))


def _lockfile_for(acme, tmp_path: Path) -> Path:
    path = tmp_path / "acme.lock.json"
    lock = {code: {"agent_id": f"agent::{code}", "llm_id": f"llm::{code}"}
            for code in [lp.code for lp in acme.languages]}
    path.write_text(json.dumps(lock), encoding="utf-8")
    return path


def test_plan_noop_when_live_matches(acme, tmp_path):
    client = _mock_client(acme)
    result = plan(acme, client=client, lockfile=_lockfile_for(acme, tmp_path))
    assert [i["action"] for i in result["items"]] == ["NOOP", "NOOP"]


def test_plan_update_diffs_changed_fields(acme, tmp_path):
    # live en-US agent has a different voice_speed; live es-419 llm has old prompt
    client = _mock_client(
        acme,
        live_overrides={
            ("agent", "en-US"): {"voice_speed": 0.5},
            ("llm", "es-419"): {"general_prompt": "old prompt"},
        },
    )
    result = plan(acme, client=client, lockfile=_lockfile_for(acme, tmp_path))
    by_code = {i["code"]: i for i in result["items"]}
    assert by_code["en-US"]["action"] == "UPDATE"
    assert "voice_speed" in by_code["en-US"]["agent_diff"]
    assert by_code["en-US"]["agent_diff"]["voice_speed"]["live"] == 0.5
    assert by_code["es-419"]["action"] == "UPDATE"
    assert "general_prompt" in by_code["es-419"]["llm_diff"]


def test_plan_update_requires_client(acme, tmp_path):
    with pytest.raises(DeployError):
        plan(acme, client=None, lockfile=_lockfile_for(acme, tmp_path))
