"""Run Layer 4 LLM-judge evals over fetched Acme calls (advisory).

Loads config/clients/acme-wellness.yaml, judges up to --max-calls transcripts via
the Anthropic Messages API, prints a per-call table + findings detail + token
usage, and writes a "judge" run record. CI never runs this — it needs an API key
and CI has none. Use --dry-run for a keyless, zero-network smoke test.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import judge_checks as jc
from dotenv import load_dotenv
from runlog import (
    DEFAULT_RUNS_DIR,
    RunRecord,
    fingerprint_key,
    prompt_fingerprint,
    write_run_record,
)

from dma_deploy_kit.agent.prompt import compile_prompt
from dma_deploy_kit.config import load_client_config

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "clients" / "acme-wellness.yaml"
CALLS_DIR = REPO_ROOT / "capture" / "calls"

# Sonnet 4.6 list pricing, per 1M tokens — advisory cost estimate only.
_PRICE_IN_PER_M = 3.0
_PRICE_OUT_PER_M = 15.0

# A canned all-pass reply for --dry-run: zero findings, zero network.
_DRY_RUN_REPLY = json.dumps({
    "verdicts": [
        {"dimension": d, "verdict": "pass", "cited_turn_indices": [], "quote": "",
         "reason": "dry-run: not evaluated"}
        for d in jc.DIMENSIONS
    ]
})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_RUNS_DIR,
                        help="directory for eval run records (default: var/evals/runs)")
    parser.add_argument("--max-calls", type=int, default=5,
                        help="hard cap on API-judged calls per run (cost control)")
    parser.add_argument("--dry-run", action="store_true",
                        help="use a canned all-pass judge (no network, no key needed)")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any findings (default: advisory, always exit 0)")
    args = parser.parse_args(argv)

    load_dotenv()  # pick up ANTHROPIC_API_KEY from .env for a real run

    if not CONFIG_PATH.exists():
        print(f"No config at {CONFIG_PATH}; nothing to run.")
        return 0
    config = load_client_config(CONFIG_PATH)

    all_calls = sorted(CALLS_DIR.glob("*.json")) if CALLS_DIR.exists() else []
    call_files = all_calls[: max(0, args.max_calls)]
    capped = len(all_calls) - len(call_files)
    print(f"Config: {config.client.slug} | calls: {len(call_files)} judged"
          f" (of {len(all_calls)} available){' | DRY RUN' if args.dry_run else ''}")
    if capped:
        print(f"  note: {capped} call(s) skipped by --max-calls {args.max_calls}")
    if not call_files:
        print("No calls in capture/calls/ — run scripts/fetch_calls.py first.")
        return 0

    judge = jc.DebugJudge(_DRY_RUN_REPLY) if args.dry_run else jc.AnthropicJudge()

    print(f"\n{'call_id':<34} {'turns':>5}  verdict")
    print("-" * 78)

    all_findings: list[tuple[str, jc.JudgeFinding]] = []
    flat_findings: list[jc.JudgeFinding] = []
    total_in = total_out = 0
    for path in call_files:
        call = json.loads(path.read_text(encoding="utf-8"))
        call_id = call.get("call_id", path.stem)
        n_turns = len(call.get("transcript_object") or [])
        findings = jc.evaluate_call(config, call, judge)
        usage = getattr(judge, "last_usage", {}) or {}
        total_in += int(usage.get("input_tokens", 0) or 0)
        total_out += int(usage.get("output_tokens", 0) or 0)
        verdict = "PASS" if not findings else f"{len(findings)} FINDING(S): " + ", ".join(
            sorted({f.check for f in findings}))
        print(f"{call_id:<34} {n_turns:>5}  {verdict}")
        for f in findings:
            all_findings.append((call_id, f))
        flat_findings.extend(findings)

    print("\n" + "=" * 78)
    if not all_findings:
        print(f"SUMMARY: {len(call_files)} call(s), 0 findings — all clean.")
    else:
        print(f"SUMMARY: {len(all_findings)} finding(s) across {len(call_files)} call(s):")
        for call_id, f in all_findings:
            print(f"\n  [{call_id}] {f.check} (turns {f.cited_turns or '-'}): {f.message}")
            if f.quote:
                print(f"      quote: {f.quote!r}")

    est_cost = total_in / 1_000_000 * _PRICE_IN_PER_M + total_out / 1_000_000 * _PRICE_OUT_PER_M
    print(f"\ntokens: {total_in} in / {total_out} out")
    print(f"cost note: ~${est_cost:.4f} at Sonnet 4.6 list rates "
          f"(${_PRICE_IN_PER_M:.0f}/${_PRICE_OUT_PER_M:.0f} per 1M) — advisory estimate")

    fingerprints = {
        fingerprint_key(CONFIG_PATH, lp.code): prompt_fingerprint(compile_prompt(config, lp))
        for lp in config.languages
    }
    record = RunRecord.create(
        layer="judge",
        prompt_fingerprints=fingerprints,
        sources=[p.name for p in call_files],
        findings=flat_findings,
    )
    record_path = write_run_record(record, args.out_dir)
    print(f"run record: {record_path}")

    return 1 if (args.strict and flat_findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
