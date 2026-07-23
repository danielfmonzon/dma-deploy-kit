"""Tests for the Layer 3 latency budget checks and runner (evals/latency_*)."""

from __future__ import annotations

import json
from pathlib import Path

import latency_checks as lc
import pytest
import run_latency as rl
import runlog

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "config" / "client.example.yaml"


def _cat(p50: int, p90: int, num: int = 5) -> dict:
    return {"p50": p50, "p90": p90, "num": num}


# All four categories comfortably under DEFAULT_BUDGET.
def _under_latency() -> dict:
    return {
        "e2e": _cat(2000, 3000),
        "llm": _cat(1000, 2000),
        "tts": _cat(100, 200),
        "asr": _cat(300, 800),
    }


def _call(latency, call_id: str = "c", duration_ms: int = 1000) -> dict:
    return {"call_id": call_id, "duration_ms": duration_ms, "latency": latency}


# --------------------------------------------------------------------------- #
# budget pass
# --------------------------------------------------------------------------- #
def test_all_under_budget_no_findings():
    assert lc.check_call_latency(_call(_under_latency())) == []


def test_run_all_matches_check():
    call = _call(_under_latency())
    assert lc.run_all(call) == lc.check_call_latency(call)


# --------------------------------------------------------------------------- #
# each budgeted metric individually over budget
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "cat,pct,attr,over",
    [
        ("e2e", "p50", "e2e_p50", 3500),
        ("e2e", "p90", "e2e_p90", 4500),
        ("llm", "p90", "llm_p90", 3000),
        ("tts", "p90", "tts_p90", 500),
        ("asr", "p90", "asr_p90", 1500),
    ],
)
def test_metric_over_budget(cat, pct, attr, over):
    latency = _under_latency()
    latency[cat][pct] = over
    findings = lc.check_call_latency(_call(latency))
    assert len(findings) == 1  # only the tampered metric breaches
    f = findings[0]
    assert f.check == f"{attr}_over_budget"
    assert f.measured == float(over)
    assert f.budget == getattr(lc.DEFAULT_BUDGET, attr)
    assert f"{over}" in f.message or f"{over:.0f}" in f.message


# --------------------------------------------------------------------------- #
# missing data -> findings, never crash
# --------------------------------------------------------------------------- #
def test_missing_latency_key():
    findings = lc.check_call_latency({"call_id": "c"})  # no "latency"
    assert len(findings) == 1
    assert findings[0].check == "latency_data_missing"


def test_empty_latency_dict():
    findings = lc.check_call_latency({"call_id": "c", "latency": {}})
    assert findings == [lc.LatencyFinding("latency_data_missing", "call has no latency data")]


def test_category_num_zero():
    latency = _under_latency()
    latency["e2e"]["num"] = 0
    findings = lc.check_call_latency(_call(latency))
    assert "latency_category_missing:e2e" in {f.check for f in findings}
    # zeroed category contributes exactly one finding; the rest stay clean
    assert len(findings) == 1


def test_category_absent():
    latency = _under_latency()
    del latency["asr"]
    findings = lc.check_call_latency(_call(latency))
    assert "latency_category_missing:asr" in {f.check for f in findings}


# --------------------------------------------------------------------------- #
# runner integration (synthetic calls, hermetic)
# --------------------------------------------------------------------------- #
def _write_call(calls_dir: Path, name: str, latency: dict, duration_ms: int = 1000) -> None:
    call = {"call_id": name, "duration_ms": duration_ms, "latency": latency}
    (calls_dir / f"{name}.json").write_text(json.dumps(call), encoding="utf-8")


def _point_latency_runner(monkeypatch, tmp_path: Path) -> Path:
    calls_dir = tmp_path / "calls"
    calls_dir.mkdir()
    monkeypatch.setattr(rl, "CONFIG_PATH", EXAMPLE_PATH)  # tracked example config
    monkeypatch.setattr(rl, "CALLS_DIR", calls_dir)
    return calls_dir


def _over_latency() -> dict:
    latency = _under_latency()
    latency["e2e"]["p90"] = 5000  # over the 4000ms budget
    return latency


def test_run_latency_writes_record(tmp_path, monkeypatch, capsys):
    calls_dir = _point_latency_runner(monkeypatch, tmp_path)
    _write_call(calls_dir, "call_ok", _under_latency())
    out_dir = tmp_path / "runs"

    code = rl.main(["--out-dir", str(out_dir)])
    assert code == 0

    out = capsys.readouterr().out
    assert "run record:" in out
    records = list(out_dir.glob("*.json"))
    assert len(records) == 1
    rec = runlog.read_run_record(records[0])
    assert rec.layer == "latency"
    assert rec.finding_count == 0
    assert rec.sources == ["call_ok.json"]
    assert rec.prompt_fingerprints
    assert all("::" in k for k in rec.prompt_fingerprints)
    assert "config/client.example.yaml::en-US" in rec.prompt_fingerprints


def test_run_latency_strict_exits_one_on_findings(tmp_path, monkeypatch, capsys):
    calls_dir = _point_latency_runner(monkeypatch, tmp_path)
    _write_call(calls_dir, "call_bad", _over_latency())
    out_dir = tmp_path / "runs"

    code = rl.main(["--out-dir", str(out_dir), "--strict"])
    assert code == 1

    out = capsys.readouterr().out
    assert "e2e_p90_over_budget" in out
    rec = runlog.read_run_record(next(out_dir.glob("*.json")))
    assert rec.finding_count >= 1
    assert "e2e_p90_over_budget" in {f["check"] for f in rec.findings}


def test_run_latency_default_exits_zero_on_findings(tmp_path, monkeypatch):
    calls_dir = _point_latency_runner(monkeypatch, tmp_path)
    _write_call(calls_dir, "call_bad", _over_latency())
    out_dir = tmp_path / "runs"

    assert rl.main(["--out-dir", str(out_dir)]) == 0  # advisory default
