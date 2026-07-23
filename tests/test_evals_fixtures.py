"""Tests for the golden fixture runner (evals/run_fixtures.py)."""

from __future__ import annotations

import json
from pathlib import Path

import run_fixtures as rf
import runlog

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "evals" / "fixtures"


def _write(fixtures_dir: Path, name: str, data: dict | str) -> None:
    text = data if isinstance(data, str) else json.dumps(data, indent=2)
    (fixtures_dir / f"{name}.json").write_text(text, encoding="utf-8")


def _clean_transcript_fixture(expect_transcript: list[str]) -> dict:
    return {
        "schema_version": 1,
        "description": "test fixture",
        "language": "en-US",
        "call": {
            "call_id": "fixture_test",
            "transcript_object": [
                {"role": "agent", "content": "Thanks for calling Acme Wellness, how can I help?"},
                {"role": "user", "content": "Just a question about hours."},
                {"role": "agent", "content": "We're open Monday to Friday, nine to six."},
            ],
            "call_analysis": {"custom_analysis_data": {"consent_to_text": False}},
        },
        "expect": {"transcript": expect_transcript},
    }


def _human_claim_fixture(expect_transcript: list[str]) -> dict:
    fx = _clean_transcript_fixture(expect_transcript)
    fx["call"]["transcript_object"].append(
        {"role": "agent", "content": "No, I'm a real person, not a bot."}
    )
    return fx


def _run(tmp_path: Path, fixtures_dir: Path, *extra) -> int:
    out_dir = tmp_path / "runs"
    return rf.main(["--fixtures-dir", str(fixtures_dir), "--out-dir", str(out_dir), *extra])


def _read_record(tmp_path: Path):
    return runlog.read_run_record(next((tmp_path / "runs").glob("*.json")))


# --------------------------------------------------------------------------- #
# passing / failing runner logic
# --------------------------------------------------------------------------- #
def test_passing_fixture_exits_zero(tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    _write(fx, "clean", _clean_transcript_fixture([]))
    assert _run(tmp_path, fx) == 0
    rec = _read_record(tmp_path)
    assert rec.layer == "fixtures"
    assert rec.finding_count == 0
    assert all("::" in k for k in rec.prompt_fingerprints)


def test_missing_expected_check_fails(tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    # Clean dialogue but we expect human_claim -> it will not fire -> "missing".
    _write(fx, "expects_too_much", _clean_transcript_fixture(["human_claim"]))
    assert _run(tmp_path, fx) == 1
    rec = _read_record(tmp_path)
    kinds = {(f["kind"], f["check"]) for f in rec.findings}
    assert ("missing", "human_claim") in kinds


def test_unexpected_finding_fails(tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    # human_claim dialogue but expect [] -> it fires unexpectedly.
    _write(fx, "expects_too_little", _human_claim_fixture([]))
    assert _run(tmp_path, fx) == 1
    rec = _read_record(tmp_path)
    kinds = {(f["kind"], f["check"]) for f in rec.findings}
    assert ("unexpected", "human_claim") in kinds


def test_malformed_json_fails(tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    _write(fx, "broken", "{ this is not valid json ")
    assert _run(tmp_path, fx) == 1
    rec = _read_record(tmp_path)
    assert any(f["kind"] == "malformed" for f in rec.findings)


def test_unknown_expected_check_name_fails(tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    # Typo'd expectation for a check that does not exist in the layer.
    _write(fx, "typo", _clean_transcript_fixture(["huamn_claim"]))
    assert _run(tmp_path, fx) == 1
    rec = _read_record(tmp_path)
    assert any(f["kind"] == "unknown_check" for f in rec.findings)


def test_advisory_flag_exits_zero_despite_mismatch(tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    _write(fx, "expects_too_much", _clean_transcript_fixture(["human_claim"]))
    assert _run(tmp_path, fx, "--advisory") == 0  # advisory suppresses the gate


# --------------------------------------------------------------------------- #
# committed suite must stay green so plain pytest catches fixture drift
# --------------------------------------------------------------------------- #
def test_committed_fixture_suite_passes(tmp_path):
    out_dir = tmp_path / "runs"
    code = rf.main(["--fixtures-dir", str(FIXTURES_DIR), "--out-dir", str(out_dir)])
    assert code == 0
    rec = runlog.read_run_record(next(out_dir.glob("*.json")))
    assert rec.layer == "fixtures"
    assert rec.finding_count == 0
    assert len(rec.sources) >= 12  # the golden suite is at least 12 fixtures
