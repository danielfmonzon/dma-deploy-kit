"""Tests for the prompt compiler."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import yaml

from dma_deploy_kit.agent import compile_all, compile_prompt
from dma_deploy_kit.agent.prompt import SECTION_ORDER
from dma_deploy_kit.config import ClientConfig, load_client_config

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "config" / "client.example.yaml"

HEADING_RE = re.compile(r"^# (.+)$", re.MULTILINE)


@pytest.fixture
def acme() -> ClientConfig:
    return load_client_config(EXAMPLE_PATH)


def parse_sections(prompt: str) -> dict[str, str]:
    """Split a compiled prompt into {heading: body} using '# HEADING' lines."""
    parts = re.split(r"^# (.+)$", prompt, flags=re.MULTILINE)
    # parts[0] is any preamble (empty); then alternating heading, body, ...
    sections: dict[str, str] = {}
    for i in range(1, len(parts), 2):
        sections[parts[i].strip()] = parts[i + 1].strip()
    return sections


def headings(prompt: str) -> list[str]:
    return HEADING_RE.findall(prompt)


def _minimal_config_dict() -> dict:
    return {
        "client": {
            "slug": "tiny-co",
            "business_name": "Tiny Co",
            "vertical": "salon",
            "timezone": "America/New_York",
        },
        "languages": [
            {"code": "en-US", "voice_id": "retell-Tamsin", "greeting": "Hi there."}
        ],
        "facts": {"description": "A tiny business."},
        "escalation": {"contact_name": "Sam"},
        "post_call": [
            {"name": "caller_name", "type": "string", "description": "Caller name."}
        ],
    }


def _load_from_dict(tmp_path: Path, data: dict) -> ClientConfig:
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return load_client_config(p)


# --------------------------------------------------------------------------- #
def test_thirteen_headings_in_order_both_languages(acme):
    for lp in acme.languages:
        prompt = compile_prompt(acme, lp)
        assert headings(prompt) == SECTION_ORDER
    assert len(SECTION_ORDER) == 13


def test_determinism_byte_identical(acme):
    en = acme.languages[0]
    assert compile_prompt(acme, en) == compile_prompt(acme, en)


def test_en_es_differ_only_in_language_and_sample_lines(acme):
    # The example configures per-language sample_lines, so EN/ES legitimately
    # differ in both LANGUAGE and SAMPLE LINES — and nowhere else.
    assert acme.languages[0].sample_lines and acme.languages[1].sample_lines
    prompts = compile_all(acme)
    en = parse_sections(prompts["en-US"])
    es = parse_sections(prompts["es-419"])
    assert set(en) == set(es) == set(SECTION_ORDER)
    differing = [name for name in SECTION_ORDER if en[name] != es[name]]
    assert differing == ["LANGUAGE", "SAMPLE LINES"]


def test_en_es_differ_only_in_language_when_no_sample_lines(tmp_path):
    # Without per-language sample_lines, the engine fallback is language-neutral,
    # so the two languages differ only in LANGUAGE.
    data = _minimal_config_dict()
    data["languages"].append(
        {"code": "es-419", "voice_id": "retell-Marta", "greeting": "Hola."}
    )
    config = _load_from_dict(tmp_path, data)
    prompts = compile_all(config)
    en = parse_sections(prompts["en-US"])
    es = parse_sections(prompts["es-419"])
    differing = [name for name in SECTION_ORDER if en[name] != es[name]]
    assert differing == ["LANGUAGE"]


def test_facts_render_present_fields(acme):
    prompt = compile_prompt(acme, acme.languages[0])
    facts = parse_sections(prompt)["FACTS"]
    assert acme.facts.address in facts
    assert acme.facts.phone in facts
    # an faq question appears
    assert acme.facts.faq[0].q in facts


def test_facts_omitting_optionals_keeps_structure(tmp_path):
    data = _minimal_config_dict()  # facts has description only
    config = _load_from_dict(tmp_path, data)
    prompt = compile_prompt(config, config.languages[0])
    assert headings(prompt) == SECTION_ORDER  # structure intact
    facts = parse_sections(prompt)["FACTS"]
    assert "Address:" not in facts
    assert "Phone:" not in facts
    assert "Frequently asked:" not in facts
    assert "A tiny business." in facts


def test_sms_consent_true_includes_texting_flow(acme):
    # example has url + sms_consent true
    booking = parse_sections(compile_prompt(acme, acme.languages[0]))["BOOKING / SMS CONSENT"]
    assert "text" in booking.lower()
    assert "permission" in booking.lower() or "can i text" in booking.lower()


def test_sms_consent_false_removes_texting_flow(tmp_path):
    data = _minimal_config_dict()
    data["booking"] = {"url": "https://tiny.example.com/book", "sms_consent": False}
    config = _load_from_dict(tmp_path, data)
    booking = parse_sections(compile_prompt(config, config.languages[0]))["BOOKING / SMS CONSENT"]
    assert "not offer to send a text" in booking.lower()


def test_booking_url_absent_degrades_gracefully(tmp_path):
    data = _minimal_config_dict()  # no booking block at all
    config = _load_from_dict(tmp_path, data)
    booking = parse_sections(compile_prompt(config, config.languages[0]))["BOOKING / SMS CONSENT"]
    assert "not set up for this line" in booking.lower()
    assert "do not promise a link" in booking.lower()


def test_medical_adjacent_preset_injects_engine_block(acme):
    hard = parse_sections(compile_prompt(acme, acme.languages[0]))["HARD RULES"]
    assert "diagnose" in hard.lower()
    assert "fda" in hard.lower()


def test_preset_none_has_no_medical_block(tmp_path):
    data = _minimal_config_dict()  # guardrails default -> preset "none"
    config = _load_from_dict(tmp_path, data)
    hard = parse_sections(compile_prompt(config, config.languages[0]))["HARD RULES"]
    assert "diagnose" not in hard.lower()
    assert "fda" not in hard.lower()


def test_every_never_say_entry_appears_in_hard_rules(acme):
    hard = parse_sections(compile_prompt(acme, acme.languages[0]))["HARD RULES"]
    assert acme.guardrails.never_say  # sanity: example has some
    for entry in acme.guardrails.never_say:
        assert entry in hard


def test_no_placeholder_artifacts(acme):
    for prompt in compile_all(acme).values():
        assert "{" not in prompt
        assert "}" not in prompt


def test_post_call_caller_fields_in_capturing_details(acme):
    cap = parse_sections(compile_prompt(acme, acme.languages[0]))["CAPTURING DETAILS"]
    caller = [f for f in acme.post_call if f.source == "caller"]
    derived = [f for f in acme.post_call if f.source == "derived"]
    assert caller  # sanity: example has caller fields
    for field in caller:
        assert field.name in cap
    # derived fields are NOT enumerated in CAPTURING DETAILS
    assert derived  # sanity: example marks call_summary as derived
    for field in derived:
        assert field.name not in cap
    # and there is one closing note that derived fields exist
    assert "derived automatically" in cap.lower()


def test_no_derived_note_when_all_caller(tmp_path):
    data = _minimal_config_dict()  # single caller field, no derived
    config = _load_from_dict(tmp_path, data)
    cap = parse_sections(compile_prompt(config, config.languages[0]))["CAPTURING DETAILS"]
    assert "caller_name" in cap
    assert "derived automatically" not in cap.lower()


def test_escalation_contact_appears(acme):
    prompt = compile_prompt(acme, acme.languages[0])
    esc = parse_sections(prompt)["ESCALATION"]
    assert acme.escalation.contact_name in esc


def test_assistant_name_in_identity_when_set(acme):
    assert acme.client.assistant_name == "Ava"
    identity = parse_sections(compile_prompt(acme, acme.languages[0]))["IDENTITY"]
    assert "Ava" in identity


def test_identity_generic_when_no_assistant_name(tmp_path):
    data = _minimal_config_dict()  # no assistant_name
    config = _load_from_dict(tmp_path, data)
    assert config.client.assistant_name is None
    identity = parse_sections(compile_prompt(config, config.languages[0]))["IDENTITY"]
    assert "You are the virtual phone receptionist for Tiny Co" in identity
    assert "None" not in identity


def test_client_sample_lines_render(acme):
    prompts = compile_all(acme)
    en_samples = parse_sections(prompts["en-US"])["SAMPLE LINES"]
    for line in acme.languages[0].sample_lines:
        assert line in en_samples
    es_samples = parse_sections(prompts["es-419"])["SAMPLE LINES"]
    for line in acme.languages[1].sample_lines:
        assert line in es_samples
    # a configured EN line should NOT leak into the ES section
    assert acme.languages[0].sample_lines[0] not in es_samples


def test_sample_lines_engine_fallback_uses_assistant_name(tmp_path):
    # no sample_lines, but assistant_name set -> engine defaults name the persona
    data = _minimal_config_dict()
    data["client"]["assistant_name"] = "Robin"
    config = _load_from_dict(tmp_path, data)
    samples = parse_sections(compile_prompt(config, config.languages[0]))["SAMPLE LINES"]
    assert "Opening:" in samples  # engine default framing
    assert "Robin" in samples
    assert "Tiny Co" in samples


def test_sample_lines_engine_fallback_without_assistant_name(tmp_path):
    data = _minimal_config_dict()  # no sample_lines, no assistant_name
    config = _load_from_dict(tmp_path, data)
    samples = parse_sections(compile_prompt(config, config.languages[0]))["SAMPLE LINES"]
    assert "Opening:" in samples
    assert "Tiny Co" in samples


def test_helper_isolation():
    a = _minimal_config_dict()
    b = copy.deepcopy(a)
    a["client"]["slug"] = "changed"
    assert b["client"]["slug"] == "tiny-co"
