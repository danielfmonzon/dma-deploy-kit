"""Tests for the regression compare CLI (evals/compare_runs.py)."""

from __future__ import annotations

from datetime import UTC, datetime

import compare_runs as cr
import runlog
from static_checks import Finding

BASE_FP = {
    "config/client.example.yaml::en-US": "aaaa",
    "config/client.example.yaml::es-419": "bbbb",
}


def _write(tmp_path, *, layer="static", fingerprints=None, sources=None, findings=None, now=None):
    record = runlog.RunRecord.create(
        layer=layer,
        prompt_fingerprints=fingerprints if fingerprints is not None else dict(BASE_FP),
        sources=sources if sources is not None else ["config/client.example.yaml"],
        findings=findings or [],
        git_cwd=tmp_path,  # -> git_commit "unknown", deterministic
        now=now,
    )
    return runlog.write_run_record(record, tmp_path)


def _finding(check="never_say_missing", language="en-US", message="boom"):
    return Finding(check, language, message)


# --------------------------------------------------------------------------- #
# explicit-path comparisons
# --------------------------------------------------------------------------- #
def test_identical_records_ok(tmp_path, capsys):
    a = _write(tmp_path, now=datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC))
    b = _write(tmp_path, now=datetime(2026, 7, 23, 11, 0, 0, tzinfo=UTC))
    code = cr.main([str(a), str(b)])
    out = capsys.readouterr().out
    assert code == 0
    assert "VERDICT: OK" in out


def test_candidate_adds_finding_is_regression(tmp_path, capsys):
    a = _write(tmp_path, findings=[], now=datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC))
    b = _write(tmp_path, findings=[_finding(message="new problem")],
               now=datetime(2026, 7, 23, 11, 0, 0, tzinfo=UTC))
    code = cr.main([str(a), str(b)])
    out = capsys.readouterr().out
    assert code == 1
    assert "VERDICT: REGRESSION — 1 new finding(s)" in out
    assert "new problem" in out  # the finding is printed in full
    assert "never_say_missing" in out


def test_resolved_finding_is_ok(tmp_path, capsys):
    a = _write(tmp_path, findings=[_finding(message="old problem")],
               now=datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC))
    b = _write(tmp_path, findings=[], now=datetime(2026, 7, 23, 11, 0, 0, tzinfo=UTC))
    code = cr.main([str(a), str(b)])
    out = capsys.readouterr().out
    assert code == 0
    assert "VERDICT: OK" in out
    assert "1 resolved" in out
    assert "resolved: never_say_missing" in out


def test_changed_fingerprint_same_findings_ok(tmp_path, capsys):
    changed = dict(BASE_FP)
    changed["config/client.example.yaml::en-US"] = "cccc"
    f = [_finding()]
    a = _write(tmp_path, findings=f, now=datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC))
    b = _write(tmp_path, fingerprints=changed, findings=f,
               now=datetime(2026, 7, 23, 11, 0, 0, tzinfo=UTC))
    code = cr.main([str(a), str(b)])
    out = capsys.readouterr().out
    assert code == 0
    assert "1 changed" in out  # prompt-change summary shown
    assert "CHANGED" in out
    assert "NOTE: prompts changed but the finding set is identical" in out


def test_differing_sources_emits_note(tmp_path, capsys):
    a = _write(tmp_path, sources=["call_a.json"], now=datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC))
    b = _write(tmp_path, sources=["call_b.json"], now=datetime(2026, 7, 23, 11, 0, 0, tzinfo=UTC))
    code = cr.main([str(a), str(b)])
    out = capsys.readouterr().out
    assert code == 0
    assert "NOTE: sources differ between runs" in out


def test_layer_mismatch_explicit_is_error(tmp_path):
    a = _write(tmp_path, layer="static", now=datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC))
    b = _write(tmp_path, layer="transcript", now=datetime(2026, 7, 23, 11, 0, 0, tzinfo=UTC))
    assert cr.main([str(a), str(b)]) == 2


# --------------------------------------------------------------------------- #
# --latest selection
# --------------------------------------------------------------------------- #
def test_latest_picks_two_newest(tmp_path, capsys):
    # Three static records; the newest two must be selected.
    _write(tmp_path, findings=[_finding(message="oldest")],
           now=datetime(2026, 7, 23, 8, 0, 0, tzinfo=UTC))
    _write(tmp_path, findings=[], now=datetime(2026, 7, 23, 9, 0, 0, tzinfo=UTC))       # baseline
    _write(tmp_path, findings=[_finding(message="newest regression")],
           now=datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC))                              # candidate
    code = cr.main(["--layer", "static", "--latest", "--runs-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1  # newest adds a finding vs the 9:00 baseline
    assert "newest regression" in out
    assert "oldest" not in out  # the 8:00 record was not part of the compare


def test_latest_needs_two_records(tmp_path):
    _write(tmp_path, layer="latency", now=datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC))
    assert cr.main(["--layer", "latency", "--latest", "--runs-dir", str(tmp_path)]) == 2


def test_latest_requires_layer(tmp_path):
    assert cr.main(["--latest", "--runs-dir", str(tmp_path)]) == 2


def test_no_args_is_error(tmp_path):
    assert cr.main([]) == 2
