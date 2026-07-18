"""Tests for the client config schema and loader."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from dma_deploy_kit.config import ClientConfig, ClientConfigError, load_client_config

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "config" / "client.example.yaml"


def _minimal_config() -> dict:
    """The fewest fields that still form a valid config."""
    return {
        "client": {
            "slug": "tiny-co",
            "business_name": "Tiny Co",
            "vertical": "salon",
            "timezone": "America/New_York",
        },
        "languages": [
            {
                "code": "en-US",
                "voice_id": "retell-Tamsin",
                "greeting": "Hi, thanks for calling.",
            }
        ],
        "facts": {"description": "A tiny business."},
        "escalation": {"contact_name": "Sam"},
        "post_call": [
            {"name": "caller_name", "type": "string", "description": "Caller name."}
        ],
    }


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "client.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# success cases
# --------------------------------------------------------------------------- #
def test_example_yaml_loads_and_validates():
    """Guards the shipped example against schema drift."""
    config = load_client_config(EXAMPLE_PATH)
    assert isinstance(config, ClientConfig)
    assert config.client.slug == "acme-wellness"
    assert [lp.code for lp in config.languages] == ["en-US", "es-419"]
    assert config.guardrails.preset == "medical_adjacent"
    assert len(config.post_call) == 8
    # exactly one enum field, and it carries choices
    enums = [f for f in config.post_call if f.type == "enum"]
    assert len(enums) == 1
    assert enums[0].choices


def test_minimal_config_loads(tmp_path):
    p = _write(tmp_path, _minimal_config())
    config = load_client_config(p)
    assert config.client.slug == "tiny-co"
    # defaults applied
    assert config.booking.sms_consent is False
    assert config.guardrails.preset == "none"
    assert config.agent.max_call_duration_ms == 600_000
    assert config.agent.expressive_emotion_tags == []


# --------------------------------------------------------------------------- #
# failure cases — each must name the offending path/field in the message
# --------------------------------------------------------------------------- #
def test_duplicate_language_codes(tmp_path):
    data = _minimal_config()
    data["languages"].append(
        {"code": "en-US", "voice_id": "retell-Tamsin", "greeting": "Hi again."}
    )
    p = _write(tmp_path, data)
    with pytest.raises(ClientConfigError) as exc:
        load_client_config(p)
    msg = str(exc.value)
    assert "languages" in msg
    assert "duplicate" in msg.lower()


def test_enum_field_without_choices(tmp_path):
    data = _minimal_config()
    data["post_call"].append(
        {"name": "urgency", "type": "enum", "description": "How urgent."}
    )
    p = _write(tmp_path, data)
    with pytest.raises(ClientConfigError) as exc:
        load_client_config(p)
    msg = str(exc.value)
    assert "post_call" in msg
    assert "choices" in msg


def test_unknown_top_level_key(tmp_path):
    data = _minimal_config()
    data["mystery"] = {"foo": "bar"}
    p = _write(tmp_path, data)
    with pytest.raises(ClientConfigError) as exc:
        load_client_config(p)
    msg = str(exc.value)
    assert "mystery" in msg
    assert "extra" in msg.lower() or "not permitted" in msg.lower()


def test_invalid_slug(tmp_path):
    data = _minimal_config()
    data["client"]["slug"] = "Acme_Wellness!"  # uppercase + underscore + bang
    p = _write(tmp_path, data)
    with pytest.raises(ClientConfigError) as exc:
        load_client_config(p)
    msg = str(exc.value)
    assert "client.slug" in msg


def test_empty_languages_list(tmp_path):
    data = _minimal_config()
    data["languages"] = []
    p = _write(tmp_path, data)
    with pytest.raises(ClientConfigError) as exc:
        load_client_config(p)
    msg = str(exc.value)
    assert "languages" in msg


def test_invalid_timezone(tmp_path):
    data = _minimal_config()
    data["client"]["timezone"] = "Mars/Olympus_Mons"
    p = _write(tmp_path, data)
    with pytest.raises(ClientConfigError) as exc:
        load_client_config(p)
    msg = str(exc.value)
    assert "client.timezone" in msg


def test_error_lists_all_failures_at_once(tmp_path):
    """Multiple mistakes should be reported together, not one at a time."""
    data = _minimal_config()
    data["client"]["slug"] = "BAD SLUG"
    data["client"]["timezone"] = "Nowhere/Void"
    p = _write(tmp_path, data)
    with pytest.raises(ClientConfigError) as exc:
        load_client_config(p)
    msg = str(exc.value)
    assert "client.slug" in msg
    assert "client.timezone" in msg


def test_extra_key_in_nested_model(tmp_path):
    data = _minimal_config()
    data["client"]["nickname"] = "acme"  # not a ClientMeta field
    p = _write(tmp_path, data)
    with pytest.raises(ClientConfigError) as exc:
        load_client_config(p)
    assert "nickname" in str(exc.value)


def test_deep_copy_isolation():
    """Sanity: helper returns independent dicts."""
    a = _minimal_config()
    b = copy.deepcopy(a)
    a["client"]["slug"] = "changed"
    assert b["client"]["slug"] == "tiny-co"
