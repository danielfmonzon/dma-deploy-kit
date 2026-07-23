"""Tests for the eval run-record core (evals/runlog.py) and runner integration."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import run_static as rs
import run_transcripts as rt
import runlog
from static_checks import Finding

from dma_deploy_kit.agent.prompt import compile_prompt
from dma_deploy_kit.config import load_client_config

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "config" / "client.example.yaml"


@pytest.fixture
def acme():
    return load_client_config(EXAMPLE_PATH)


# --------------------------------------------------------------------------- #
# prompt_fingerprint stability
# --------------------------------------------------------------------------- #
def test_fingerprint_same_input_same_hex():
    assert runlog.prompt_fingerprint("hello world") == runlog.prompt_fingerprint("hello world")


def test_fingerprint_different_input_different_hex():
    assert runlog.prompt_fingerprint("a") != runlog.prompt_fingerprint("b")


def test_fingerprint_is_sha256_hex():
    fp = runlog.prompt_fingerprint("anything")
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_of_compiled_prompt_stable(acme):
    lp = acme.languages[0]
    first = runlog.prompt_fingerprint(compile_prompt(acme, lp))
    second = runlog.prompt_fingerprint(compile_prompt(acme, lp))
    assert first == second


# --------------------------------------------------------------------------- #
# fingerprint_key — path-qualified, POSIX, collision-free
# --------------------------------------------------------------------------- #
def test_fingerprint_key_format_and_posix():
    # Absolute path under the repo root collapses to a relative POSIX key.
    abs_path = REPO_ROOT / "config" / "client.example.yaml"
    assert runlog.fingerprint_key(abs_path, "en-US") == "config/client.example.yaml::en-US"
    # A relative path is used as-is (still POSIX-normalized).
    assert (
        runlog.fingerprint_key("config/clients/acme-wellness.yaml", "es-419")
        == "config/clients/acme-wellness.yaml::es-419"
    )


def test_fingerprint_key_distinct_for_same_slug_different_paths():
    # Both files carry slug "acme-wellness"; the path qualifier keeps keys distinct.
    k1 = runlog.fingerprint_key("config/client.example.yaml", "en-US")
    k2 = runlog.fingerprint_key("config/clients/acme-wellness.yaml", "en-US")
    assert k1 != k2


# --------------------------------------------------------------------------- #
# RunRecord round-trip
# --------------------------------------------------------------------------- #
def test_run_record_round_trip(tmp_path):
    findings = [Finding("never_say_missing", "en-US", "boom")]
    record = runlog.RunRecord.create(
        layer="static",
        prompt_fingerprints={"acme/en-US": "deadbeef"},
        sources=["config/client.example.yaml"],
        findings=findings,
        git_cwd=tmp_path,  # outside a repo -> git_commit "unknown", deterministic
    )
    path = runlog.write_run_record(record, tmp_path)
    loaded = runlog.read_run_record(path)
    assert loaded == record
    assert loaded.layer == "static"
    assert loaded.finding_count == 1
    assert loaded.findings == [
        {"check": "never_say_missing", "language": "en-US", "message": "boom"}
    ]


def test_read_run_record_unknown_schema_raises(tmp_path):
    record = runlog.RunRecord.create(
        layer="static", prompt_fingerprints={}, sources=[], findings=[], git_cwd=tmp_path
    )
    path = runlog.write_run_record(record, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = 999
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        runlog.read_run_record(path)


# --------------------------------------------------------------------------- #
# git_commit fallback — never crashes, yields "unknown"
# --------------------------------------------------------------------------- #
def test_git_commit_fallback_outside_repo(tmp_path):
    assert runlog.git_short_commit(cwd=tmp_path) == "unknown"


def test_git_commit_fallback_on_subprocess_error(monkeypatch):
    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr(runlog.subprocess, "run", boom)
    assert runlog.git_short_commit() == "unknown"  # no exception


def test_run_record_create_uses_git_fallback(tmp_path):
    record = runlog.RunRecord.create(
        layer="static", prompt_fingerprints={}, sources=[], findings=[], git_cwd=tmp_path
    )
    assert record.git_commit == "unknown"


# --------------------------------------------------------------------------- #
# run_static.py integration
# --------------------------------------------------------------------------- #
def test_run_static_writes_record(tmp_path, monkeypatch, capsys):
    # Pin to the tracked example only, so the run is deterministic regardless of
    # any local config/clients/*.yaml present in a dev checkout.
    monkeypatch.setattr(rs, "iter_config_paths", lambda: iter([EXAMPLE_PATH]))
    code = rs.main(["--out-dir", str(tmp_path)])
    assert code == 0  # example config is clean

    out = capsys.readouterr().out
    assert "run record:" in out

    records = list(tmp_path.glob("*.json"))
    assert len(records) == 1
    rec = runlog.read_run_record(records[0])
    assert rec.layer == "static"
    assert rec.prompt_fingerprints  # non-empty
    assert all(len(fp) == 64 for fp in rec.prompt_fingerprints.values())
    assert all("::" in k for k in rec.prompt_fingerprints)  # path::code key form
    assert "config/client.example.yaml::en-US" in rec.prompt_fingerprints
    assert rec.finding_count == 0
    assert rec.findings == []


# --------------------------------------------------------------------------- #
# run_transcripts.py integration (synthetic calls, hermetic)
# --------------------------------------------------------------------------- #
def _write_call(calls_dir: Path, name: str, agent_turn: str) -> None:
    call = {
        "call_id": name,
        "agent_id": "agent_x",
        "transcript_object": [
            {"role": "agent", "content": agent_turn},
            {"role": "user", "content": "okay"},
        ],
        "call_analysis": {"custom_analysis_data": {}},
    }
    (calls_dir / f"{name}.json").write_text(json.dumps(call), encoding="utf-8")


def _point_transcript_runner(monkeypatch, tmp_path: Path) -> Path:
    calls_dir = tmp_path / "calls"
    calls_dir.mkdir()
    # Example config is tracked; no lockfile -> language resolves to "?" (fine).
    monkeypatch.setattr(rt, "CONFIG_PATH", EXAMPLE_PATH)
    monkeypatch.setattr(rt, "LOCKFILE", tmp_path / "missing.lock.json")
    monkeypatch.setattr(rt, "CALLS_DIR", calls_dir)
    return calls_dir


def test_run_transcripts_clean_writes_record(tmp_path, monkeypatch, capsys):
    calls_dir = _point_transcript_runner(monkeypatch, tmp_path)
    _write_call(calls_dir, "call_clean", "Thanks for calling Acme Wellness, how can I help today?")
    out_dir = tmp_path / "runs"

    code = rt.main(["--out-dir", str(out_dir)])
    assert code == 0

    out = capsys.readouterr().out
    assert "run record:" in out
    records = list(out_dir.glob("*.json"))
    assert len(records) == 1
    rec = runlog.read_run_record(records[0])
    assert rec.layer == "transcript"
    assert rec.prompt_fingerprints
    assert all("::" in k for k in rec.prompt_fingerprints)  # path::code key form
    assert "config/client.example.yaml::en-US" in rec.prompt_fingerprints
    assert rec.finding_count == 0
    assert rec.sources == ["call_clean.json"]


def test_run_transcripts_strict_exits_one_on_findings(tmp_path, monkeypatch, capsys):
    calls_dir = _point_transcript_runner(monkeypatch, tmp_path)
    _write_call(calls_dir, "call_bad", "No, I'm a real person, I promise!")
    out_dir = tmp_path / "runs"

    code = rt.main(["--out-dir", str(out_dir), "--strict"])
    assert code == 1  # findings + --strict -> nonzero

    out = capsys.readouterr().out
    assert "human_claim" in out  # console findings
    rec = runlog.read_run_record(next(out_dir.glob("*.json")))
    assert rec.finding_count >= 1
    checks = {f["check"] for f in rec.findings}
    assert "human_claim" in checks  # serialization matches console


def test_run_transcripts_findings_advisory_exits_zero(tmp_path, monkeypatch):
    calls_dir = _point_transcript_runner(monkeypatch, tmp_path)
    _write_call(calls_dir, "call_bad", "No, I'm a real person, I promise!")
    out_dir = tmp_path / "runs"

    code = rt.main(["--out-dir", str(out_dir)])  # no --strict
    assert code == 0  # advisory default: findings do not fail the run


def test_run_transcripts_strict_clean_exits_zero(tmp_path, monkeypatch):
    calls_dir = _point_transcript_runner(monkeypatch, tmp_path)
    _write_call(calls_dir, "call_clean", "Thanks for calling Acme Wellness, how can I help today?")
    out_dir = tmp_path / "runs"

    code = rt.main(["--out-dir", str(out_dir), "--strict"])
    assert code == 0  # clean + --strict -> still zero
