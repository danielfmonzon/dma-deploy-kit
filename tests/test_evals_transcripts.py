"""Tests for Layer 2 deterministic transcript checks."""

from __future__ import annotations

from pathlib import Path

import pytest
import transcript_checks as tc

from dma_deploy_kit.config import load_client_config

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "config" / "client.example.yaml"


@pytest.fixture
def acme():
    return load_client_config(EXAMPLE_PATH)


def _turns(*pairs):
    return [{"role": role, "content": content} for role, content in pairs]


def _meta(language="en-US", consent=True):
    return {
        "agent_id": "agent_x",
        "language": language,
        "custom_analysis_data": {"consent_to_text": consent},
    }


def _checks(findings):
    return {f.check for f in findings}


# --------------------------------------------------------------------------- #
# clean transcript
# --------------------------------------------------------------------------- #
def test_clean_transcript_zero_findings(acme):
    turns = _turns(
        ("agent", "Thanks for calling Acme Wellness, this is Ava. How can I help you today?"),
        ("user", "I'd like to book a facial."),
        ("agent", "Wonderful, I can help with that. Can I text you the booking link?"),
        ("user", "Yes please."),
    )
    assert tc.run_all(acme, turns, _meta(consent=True)) == []


# --------------------------------------------------------------------------- #
# human claim
# --------------------------------------------------------------------------- #
def test_human_claim_triggers(acme):
    turns = _turns(
        ("agent", "Thanks for calling Acme Wellness."),
        ("user", "Are you a robot?"),
        ("agent", "No, I am a real person, I promise!"),
    )
    findings = tc.check_human_claim(acme, turns, _meta())
    assert any(f.check == "human_claim" for f in findings)
    assert "real person" in findings[0].quote


def test_human_claim_does_not_flag_benign_person_mention(acme):
    turns = _turns(("agent", "I'll have a person from our team follow up with you."))
    assert tc.check_human_claim(acme, turns, _meta()) == []


# --------------------------------------------------------------------------- #
# SMS promise without consent
# --------------------------------------------------------------------------- #
def test_sms_promise_without_consent_triggers(acme):
    turns = _turns(
        ("agent", "Great, I'll text you the booking link right now."),
    )
    findings = tc.check_sms_promise_without_consent(acme, turns, _meta(consent=False))
    assert any(f.check == "sms_promise_no_consent" for f in findings)


def test_sms_promise_with_consent_ok(acme):
    turns = _turns(("agent", "Great, I'll text you the booking link right now."))
    assert tc.check_sms_promise_without_consent(acme, turns, _meta(consent=True)) == []


def test_sms_promise_absent_consent_field_triggers(acme):
    turns = _turns(("agent", "I'll send you the link by text."))
    meta = {"agent_id": "a", "language": "en-US", "custom_analysis_data": {}}  # no consent captured
    findings = tc.check_sms_promise_without_consent(acme, turns, meta)
    assert any(f.check == "sms_promise_no_consent" for f in findings)


# --------------------------------------------------------------------------- #
# forbidden phrase
# --------------------------------------------------------------------------- #
def test_forbidden_phrase_triggers(acme):
    # "FDA-approved" is derivable from Acme's never_say instruction (hyphenated)
    assert "FDA-approved" in tc.forbidden_tokens(acme)
    turns = _turns(("agent", "Our treatment is FDA-approved and totally safe."))
    findings = tc.check_forbidden_phrases(acme, turns, _meta())
    assert any(f.check == "forbidden_phrase" for f in findings)
    assert "FDA-approved" in findings[0].message


def test_forbidden_tokens_skip_instructions_whole(acme):
    # No whole never_say instruction should appear as a token (they're skipped)
    for entry in acme.guardrails.never_say:
        assert entry not in tc.forbidden_tokens(acme)


# --------------------------------------------------------------------------- #
# greeting language
# --------------------------------------------------------------------------- #
def test_greeting_language_wrong_for_es_agent(acme):
    turns = _turns(("agent", "Hi, thanks for calling. How can I help you today?"))
    findings = tc.check_greeting_language(acme, turns, _meta(language="es-419"))
    assert any(f.check == "greeting_language" for f in findings)


def test_greeting_language_correct_es(acme):
    turns = _turns(("agent", "Gracias por llamar a Acme Wellness. ¿En qué puedo ayudarle hoy?"))
    assert tc.check_greeting_language(acme, turns, _meta(language="es-419")) == []


def test_greeting_language_correct_en(acme):
    turns = _turns(("agent", "Thanks for calling Acme Wellness, how can I help?"))
    assert tc.check_greeting_language(acme, turns, _meta(language="en-US")) == []


# --------------------------------------------------------------------------- #
# aggregate
# --------------------------------------------------------------------------- #
def test_run_all_aggregates_multiple(acme):
    turns = _turns(
        ("agent", "Hi, I'm a real person! Our facial is FDA-approved and I'll text you the link."),
    )
    findings = tc.run_all(acme, turns, _meta(language="en-US", consent=False))
    assert {"human_claim", "forbidden_phrase", "sms_promise_no_consent"} <= _checks(findings)
