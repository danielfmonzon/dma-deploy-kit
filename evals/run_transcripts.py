"""Run Layer 2 deterministic transcript checks over fetched Acme calls.

Loads config/clients/acme-wellness.yaml, iterates capture/calls/*.json, resolves
each call's language from the lockfile agent_id, runs the checks, and prints a
per-call verdict table plus a summary.

Writes an eval run record (see runlog.py). By default the runner is advisory and
always exits 0; pass --strict to exit 1 when any findings are present.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import transcript_checks as tc
from runlog import DEFAULT_RUNS_DIR, RunRecord, prompt_fingerprint, write_run_record

from dma_deploy_kit.agent.prompt import compile_prompt
from dma_deploy_kit.config import load_client_config

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "clients" / "acme-wellness.yaml"
LOCKFILE = REPO_ROOT / "config" / "clients" / "acme-wellness.lock.json"
CALLS_DIR = REPO_ROOT / "capture" / "calls"


def agent_language_map() -> dict[str, str]:
    if not LOCKFILE.exists():
        return {}
    lock = json.loads(LOCKFILE.read_text(encoding="utf-8"))
    return {ids["agent_id"]: code for code, ids in lock.items() if ids.get("agent_id")}


def turns_from_call(call: dict) -> list[dict]:
    obj = call.get("transcript_object") or []
    return [{"role": t.get("role"), "content": t.get("content") or ""} for t in obj]


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
    lang_map = agent_language_map()

    call_files = sorted(CALLS_DIR.glob("*.json")) if CALLS_DIR.exists() else []
    print(f"Config: {config.client.slug} | calls found: {len(call_files)}\n")
    if not call_files:
        print("No calls in capture/calls/ — run scripts/fetch_calls.py first.")
        return 0

    print(f"{'call_id':<34} {'agent/lang':<12} {'turns':>5}  verdict")
    print("-" * 78)

    all_findings: list[tuple[str, tc.TranscriptFinding]] = []
    flat_findings: list[tc.TranscriptFinding] = []
    for path in call_files:
        call = json.loads(path.read_text(encoding="utf-8"))
        call_id = call.get("call_id", path.stem)
        agent_id = call.get("agent_id", "")
        language = lang_map.get(agent_id, "?")
        turns = turns_from_call(call)
        custom = (call.get("call_analysis") or {}).get("custom_analysis_data") or {}
        meta = {"agent_id": agent_id, "language": language, "custom_analysis_data": custom}
        findings = tc.run_all(config, turns, meta)
        verdict = "PASS" if not findings else f"{len(findings)} FINDING(S): " + ", ".join(
            sorted({f.check for f in findings})
        )
        print(f"{call_id:<34} {language:<12} {len(turns):>5}  {verdict}")
        for f in findings:
            all_findings.append((call_id, f))
        flat_findings.extend(findings)

    print("\n" + "=" * 78)
    if not all_findings:
        print(f"SUMMARY: {len(call_files)} call(s), 0 findings — all clean.")
    else:
        print(f"SUMMARY: {len(all_findings)} finding(s) across {len(call_files)} call(s):")
        for call_id, f in all_findings:
            print(f"\n  [{call_id}] {f.check} (turn {f.turn_index}): {f.message}")
            if f.quote:
                print(f"      quote: {f.quote!r}")

    fingerprints = {
        f"{config.client.slug}/{lp.code}": prompt_fingerprint(compile_prompt(config, lp))
        for lp in config.languages
    }
    record = RunRecord.create(
        layer="transcript",
        prompt_fingerprints=fingerprints,
        sources=[p.name for p in call_files],
        findings=flat_findings,
    )
    record_path = write_run_record(record, args.out_dir)
    print(f"run record: {record_path}")

    return 1 if (args.strict and flat_findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
