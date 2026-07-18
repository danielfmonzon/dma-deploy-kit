"""Tests for Layer 1 static prompt checks."""

from __future__ import annotations

from pathlib import Path

import pytest
import static_checks as sc

from dma_deploy_kit.agent.prompt import compile_prompt
from dma_deploy_kit.config import load_client_config

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "config" / "client.example.yaml"


@pytest.fixture
def acme():
    return load_client_config(EXAMPLE_PATH)


def _codes(findings):
    return {f.check for f in findings}


# --------------------------------------------------------------------------- #
# clean config passes
# --------------------------------------------------------------------------- #
def test_clean_config_passes(acme):
    for lp in acme.languages:
        prompt = compile_prompt(acme, lp)
        assert sc.run_all(acme, lp.code, prompt) == []


# --------------------------------------------------------------------------- #
# broken variants
# --------------------------------------------------------------------------- #
def test_removed_never_say_from_prompt(acme):
    lp = acme.languages[0]
    prompt = compile_prompt(acme, lp)
    entry = acme.guardrails.never_say[0]
    tampered = prompt.replace(entry, "REDACTED")  # simulate a prompt missing the rule
    findings = sc.check_never_say_in_hard_rules(acme, lp.code, tampered)
    assert any(f.check == "never_say_missing" and entry in f.message for f in findings)


def test_wrong_language_greeting(acme):
    en, es = acme.languages
    bad_es = es.model_copy(update={"greeting": "Hello there, how can I help you?"})
    cfg = acme.model_copy(update={"languages": [en, bad_es]})
    prompt = compile_prompt(cfg, bad_es)
    findings = sc.check_greeting_language(cfg, "es-419", prompt)
    assert any(f.check == "greeting_language" for f in findings)
    # sanity: the correct Spanish greeting passes
    assert sc.check_greeting_language(acme, "es-419", compile_prompt(acme, es)) == []


def test_missing_heading(acme):
    lp = acme.languages[0]
    prompt = compile_prompt(acme, lp)
    broken = prompt.replace("# FACTS\n", "FACTS\n", 1)  # demote a heading
    findings = sc.check_structure(acme, lp.code, broken)
    assert any(f.check == "heading_mismatch" for f in findings)


def test_placeholder_artifact(acme):
    lp = acme.languages[0]
    prompt = compile_prompt(acme, lp) + "\nleftover {placeholder}"
    findings = sc.check_structure(acme, lp.code, prompt)
    assert any(f.check == "placeholder_artifact" for f in findings)


def test_medical_block_missing(acme):
    # acme is medical_adjacent; strip the block marker -> finding
    lp = acme.languages[0]
    prompt = compile_prompt(acme, lp).replace(sc._MEDICAL_BLOCK_MARKER, "REMOVED")
    findings = sc.check_medical_block(acme, lp.code, prompt)
    assert any(f.check == "medical_block_missing" for f in findings)


def test_medical_block_unexpected_for_preset_none(tmp_path):
    import yaml
    data = {
        "client": {"slug": "n", "business_name": "N", "vertical": "salon",
                   "timezone": "America/New_York"},
        "languages": [
            {"code": "en-US", "voice_id": "retell-Tamsin", "greeting": "Hello, thanks for calling."}
        ],
        "facts": {"description": "x"},
        "escalation": {"contact_name": "Sam"},
        "guardrails": {"preset": "none"},
        "post_call": [{"name": "caller_name", "type": "string", "description": "n"}],
    }
    p = tmp_path / "n.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    cfg = load_client_config(p)
    lp = cfg.languages[0]
    clean = compile_prompt(cfg, lp)
    assert sc.check_medical_block(cfg, lp.code, clean) == []  # no block, preset none -> ok
    injected = clean.replace("# HARD RULES\n", f"# HARD RULES\n{sc._MEDICAL_BLOCK_MARKER}\n", 1)
    findings = sc.check_medical_block(cfg, lp.code, injected)
    assert any(f.check == "medical_block_unexpected" for f in findings)


def test_escalation_entry_missing(acme):
    lp = acme.languages[0]
    entry = acme.escalation.escalate_when[0]
    prompt = compile_prompt(acme, lp).replace(entry, "REDACTED")
    findings = sc.check_escalation(acme, lp.code, prompt)
    assert any(f.check == "escalate_when_missing" for f in findings)


def test_derived_field_must_not_be_in_capturing(acme):
    # call_summary is derived; if it appeared in CAPTURING DETAILS that's a finding
    lp = acme.languages[0]
    prompt = compile_prompt(acme, lp)
    cap_injected = prompt.replace(
        "# CAPTURING DETAILS\n", "# CAPTURING DETAILS\n- call_summary: leak\n", 1
    )
    findings = sc.check_capturing_details(acme, lp.code, cap_injected)
    assert any(f.check == "derived_field_listed" for f in findings)


def test_sample_line_leak_detected(acme):
    # inject an es sample line into the en prompt's SAMPLE LINES
    en, es = acme.languages
    en_prompt = compile_prompt(acme, en)
    es_line = es.sample_lines[0]
    leaked = en_prompt.replace("# SAMPLE LINES\n", f"# SAMPLE LINES\n- {es_line}\n", 1)
    findings = sc.check_sample_lines(acme, "en-US", leaked)
    assert any(f.check == "sample_line_leak" for f in findings)
