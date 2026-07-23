"""Tests for the Layer 4 LLM-judge (evals/judge_checks.py, evals/run_judge.py).

Hermetic and zero-network: AnthropicJudge is exercised via httpx.MockTransport;
evaluate_call and the runner use DebugJudge / canned replies.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import judge_checks as jc
import pytest
import run_judge as rj
import runlog
from static_checks import parse_sections

from dma_deploy_kit.agent.prompt import compile_prompt
from dma_deploy_kit.config import load_client_config

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = REPO_ROOT / "config" / "client.example.yaml"


@pytest.fixture
def cfg():
    return load_client_config(EXAMPLE_PATH)


CALL = {
    "call_id": "fixture_judge",
    "transcript_object": [
        {"role": "agent", "content": "Thanks for calling Acme Wellness, this is Ava."},
        {"role": "user", "content": "Do you do laser hair removal?"},
        {"role": "agent", "content": "Yes, our laser package is guaranteed to remove all hair "
                                     "in one session."},
    ],
}


def _reply(verdicts: list[dict]) -> str:
    return json.dumps({"verdicts": verdicts})


def _pass(dim: str) -> dict:
    return {"dimension": dim, "verdict": "pass", "cited_turn_indices": [], "quote": "",
            "reason": "ok"}


def _all_pass() -> str:
    return _reply([_pass(d) for d in jc.DIMENSIONS])


# --------------------------------------------------------------------------- #
# rubric
# --------------------------------------------------------------------------- #
def test_rubric_includes_facts_escalation_and_rules(cfg):
    sections = parse_sections(compile_prompt(cfg, cfg.languages[0]))
    system, user_template = jc.build_rubric(cfg)
    # facts + escalation content from the config are present in the user prompt
    assert sections["FACTS"].strip()[:40] in user_template
    assert sections["ESCALATION"].strip()[:40] in user_template
    # strict-JSON instruction and the untrusted-transcript line are in the system prompt
    assert "STRICT JSON" in system
    assert "untrusted" in system.lower()
    assert "{transcript}" in user_template  # placeholder for evaluate_call to fill


# --------------------------------------------------------------------------- #
# evaluate_call via DebugJudge
# --------------------------------------------------------------------------- #
def test_all_pass_yields_no_findings(cfg):
    assert jc.evaluate_call(cfg, CALL, jc.DebugJudge(_all_pass())) == []


def test_fail_with_verifiable_quote_yields_dimension_finding(cfg):
    reply = _reply([
        _pass("booking_intent_handled"),
        {"dimension": "hallucinated_commitment", "verdict": "fail",
         "cited_turn_indices": [2], "quote": "guaranteed to remove all hair",
         "reason": "claim not in business facts"},
        _pass("unresolved_caller_request"),
    ])
    findings = jc.evaluate_call(cfg, CALL, jc.DebugJudge(reply))
    assert len(findings) == 1
    assert findings[0].check == "judge_hallucinated_commitment"
    assert findings[0].verdict == "fail"
    assert findings[0].quote == "guaranteed to remove all hair"


def test_fail_with_fabricated_quote_is_downgraded(cfg):
    reply = _reply([
        _pass("booking_intent_handled"),
        {"dimension": "hallucinated_commitment", "verdict": "fail",
         "cited_turn_indices": [2], "quote": "we offer a lifetime warranty on all treatments",
         "reason": "claim not in business facts"},
        _pass("unresolved_caller_request"),
    ])
    findings = jc.evaluate_call(cfg, CALL, jc.DebugJudge(reply))
    assert len(findings) == 1
    assert findings[0].check == "judge_citation_unverified"  # NOT the dimension finding


def test_malformed_json_yields_output_invalid(cfg):
    findings = jc.evaluate_call(cfg, CALL, jc.DebugJudge("not json at all {"))
    assert len(findings) == 1
    assert findings[0].check == "judge_output_invalid"


def test_unknown_dimension_yields_output_invalid(cfg):
    reply = _reply([{"dimension": "made_up_dimension", "verdict": "pass",
                     "cited_turn_indices": [], "quote": "", "reason": "x"}])
    findings = jc.evaluate_call(cfg, CALL, jc.DebugJudge(reply))
    assert len(findings) == 1
    assert findings[0].check == "judge_output_invalid"


# --------------------------------------------------------------------------- #
# AnthropicJudge via MockTransport (zero network)
# --------------------------------------------------------------------------- #
def test_anthropic_judge_request_shape(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "JUDGED"}],
                  "usage": {"input_tokens": 42, "output_tokens": 13}},
        )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    judge = jc.AnthropicJudge(transport=httpx.MockTransport(handler), backoff=0.0)
    out = judge.judge("system text", "user text")

    assert out == "JUDGED"
    assert captured["url"] == jc.ANTHROPIC_URL
    assert captured["headers"]["x-api-key"] == "test-key-123"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    body = captured["body"]
    assert body["model"] == "claude-sonnet-4-6"
    assert body["temperature"] == 0
    assert body["max_tokens"] == 1500
    assert body["system"] == "system text"
    assert body["messages"] == [{"role": "user", "content": "user text"}]
    assert judge.last_usage == {"input_tokens": 42, "output_tokens": 13}


def test_anthropic_judge_retries_on_429_then_200(monkeypatch):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "OK"}],
                  "usage": {"input_tokens": 5, "output_tokens": 2}},
        )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    judge = jc.AnthropicJudge(transport=httpx.MockTransport(handler), backoff=0.0)
    assert judge.judge("s", "u") == "OK"
    assert len(calls) == 2  # one retry


def test_anthropic_judge_raises_on_persistent_5xx(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    judge = jc.AnthropicJudge(transport=httpx.MockTransport(handler), backoff=0.0)
    with pytest.raises(jc.JudgeError):
        judge.judge("s", "u")


def test_anthropic_judge_requires_key():
    with pytest.raises(jc.JudgeError):
        jc.AnthropicJudge(api_key="")


# --------------------------------------------------------------------------- #
# runner (dry-run, synthetic calls, zero network)
# --------------------------------------------------------------------------- #
def _write_call(calls_dir: Path, name: str, turns) -> None:
    call = {"call_id": name,
            "transcript_object": [{"role": r, "content": c} for r, c in turns]}
    (calls_dir / f"{name}.json").write_text(json.dumps(call), encoding="utf-8")


def _point_runner(monkeypatch, tmp_path: Path) -> Path:
    calls_dir = tmp_path / "calls"
    calls_dir.mkdir()
    monkeypatch.setattr(rj, "CONFIG_PATH", EXAMPLE_PATH)
    monkeypatch.setattr(rj, "CALLS_DIR", calls_dir)
    monkeypatch.setattr(rj, "load_dotenv", lambda *a, **k: None)  # no .env, no key
    return calls_dir


def test_run_judge_dry_run_writes_record(tmp_path, monkeypatch, capsys):
    calls_dir = _point_runner(monkeypatch, tmp_path)
    _write_call(calls_dir, "call_a", [("agent", "Thanks for calling."), ("user", "hi")])
    out_dir = tmp_path / "runs"

    code = rj.main(["--dry-run", "--out-dir", str(out_dir)])
    assert code == 0  # dry-run, all-pass, advisory

    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "run record:" in out
    rec = runlog.read_run_record(next(out_dir.glob("*.json")))
    assert rec.layer == "judge"
    assert rec.finding_count == 0
    assert rec.sources == ["call_a.json"]
    assert all("::" in k for k in rec.prompt_fingerprints)


def test_run_judge_max_calls_caps_judged(tmp_path, monkeypatch):
    calls_dir = _point_runner(monkeypatch, tmp_path)
    for name in ("call_a", "call_b", "call_c"):
        _write_call(calls_dir, name, [("agent", "hi"), ("user", "yo")])
    out_dir = tmp_path / "runs"

    code = rj.main(["--dry-run", "--max-calls", "2", "--out-dir", str(out_dir)])
    assert code == 0
    rec = runlog.read_run_record(next(out_dir.glob("*.json")))
    assert len(rec.sources) == 2  # only 2 of 3 judged
