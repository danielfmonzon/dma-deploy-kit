"""Run Layer 3 latency budget checks over fetched Acme calls.

Loads config/clients/acme-wellness.yaml (for prompt fingerprints), iterates
capture/calls/*.json, compares each call's latency percentiles against the
default budget, and prints a per-call table + findings detail + summary.

Writes an eval run record (layer "latency"). Advisory by default (always exits
0); pass --strict to exit 1 when any findings are present. Latency regressions
correlate with prompt size, so the record deliberately pins the prompt versions
evaluated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import latency_checks as lc
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


def _pct(call: dict, cat: str, pct: str):
    return ((call.get("latency") or {}).get(cat) or {}).get(pct)


def _fmt(value) -> str:
    return f"{value:.0f}" if isinstance(value, (int, float)) else "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help="directory for eval run records (default: var/evals/runs)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if any findings (default: advisory, always exit 0)",
    )
    args = parser.parse_args(argv)

    if not CONFIG_PATH.exists():
        print(f"No config at {CONFIG_PATH}; nothing to run.")
        return 0
    config = load_client_config(CONFIG_PATH)

    call_files = sorted(CALLS_DIR.glob("*.json")) if CALLS_DIR.exists() else []
    print(f"Config: {config.client.slug} | calls found: {len(call_files)}\n")
    if not call_files:
        print("No calls in capture/calls/ — run scripts/fetch_calls.py first.")
        return 0

    print(f"{'call_id':<34} {'dur_ms':>7} {'e2e50':>6} {'e2e90':>6} {'llm90':>6}  verdict")
    print("-" * 78)

    all_findings: list[tuple[str, lc.LatencyFinding]] = []
    flat_findings: list[lc.LatencyFinding] = []
    for path in call_files:
        call = json.loads(path.read_text(encoding="utf-8"))
        call_id = call.get("call_id", path.stem)
        findings = lc.run_all(call)
        verdict = "OK" if not findings else f"{len(findings)} FINDING(S)"
        print(
            f"{call_id:<34} {_fmt(call.get('duration_ms')):>7} "
            f"{_fmt(_pct(call, 'e2e', 'p50')):>6} {_fmt(_pct(call, 'e2e', 'p90')):>6} "
            f"{_fmt(_pct(call, 'llm', 'p90')):>6}  {verdict}"
        )
        for f in findings:
            all_findings.append((call_id, f))
        flat_findings.extend(findings)

    print("\n" + "=" * 78)
    if not all_findings:
        print(f"SUMMARY: {len(call_files)} call(s), 0 findings — all within budget.")
    else:
        print(f"SUMMARY: {len(all_findings)} finding(s) across {len(call_files)} call(s):")
        for call_id, f in all_findings:
            detail = ""
            if f.measured is not None:
                detail = f" (measured {f.measured:.0f}ms vs {f.budget}ms)"
            print(f"\n  [{call_id}] {f.check}: {f.message}{detail}")

    fingerprints = {
        fingerprint_key(CONFIG_PATH, lp.code): prompt_fingerprint(compile_prompt(config, lp))
        for lp in config.languages
    }
    record = RunRecord.create(
        layer="latency",
        prompt_fingerprints=fingerprints,
        sources=[p.name for p in call_files],
        findings=flat_findings,
    )
    record_path = write_run_record(record, args.out_dir)
    print(f"run record: {record_path}")

    return 1 if (args.strict and flat_findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
