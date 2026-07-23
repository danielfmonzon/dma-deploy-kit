"""Run the golden fixture suite — the CI-gating regression benchmark.

Each fixture in evals/fixtures/*.json is a fully synthetic, Retell-shaped call
plus the exact set of transcript/latency check names it MUST produce. This runner
loads only config/client.example.yaml (tracked, CI-present), replays the checks
per fixture, and gates on any mismatch between expected and actual check-name
sets. A typo'd or unknown expected check name, or a malformed fixture, is a
failure — never a silent skip.

Gates by default (exit 1 on any mismatch). Pass --advisory to always exit 0.
Writes a RunRecord with layer "fixtures".
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import latency_checks as lc
import transcript_checks as tc
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
EXAMPLE_CONFIG = REPO_ROOT / "config" / "client.example.yaml"
DEFAULT_FIXTURES_DIR = REPO_ROOT / "evals" / "fixtures"

SCHEMA_VERSION = 1
# Known check names per layer, imported from each layer's own registry so the
# typo-guard can never drift from what the checks actually emit.
KNOWN_CHECK_NAMES = {
    "transcript": set(tc.CHECK_NAMES),
    "latency": set(lc.CHECK_NAMES),
}


@dataclass(frozen=True)
class FixtureMismatch:
    fixture: str
    kind: str  # "missing" | "unexpected" | "malformed" | "unknown_check"
    layer: str
    check: str
    message: str


def _validate_fixture(data: object) -> list[str]:
    """Return a list of structural error messages ([] means well-formed)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["fixture is not a JSON object"]
    if data.get("schema_version") != SCHEMA_VERSION:
        got = data.get("schema_version")
        errors.append(f"schema_version must be {SCHEMA_VERSION}, got {got!r}")
    for key in ("description", "language", "call", "expect"):
        if key not in data:
            errors.append(f"missing required key {key!r}")
    if not isinstance(data.get("language"), str) or not data.get("language"):
        errors.append("language must be a non-empty string")
    if "call" in data and not isinstance(data["call"], dict):
        errors.append("call must be an object")
    expect = data.get("expect")
    if not isinstance(expect, dict):
        errors.append("expect must be an object")
        return errors
    if not ({"transcript", "latency"} & set(expect)):
        errors.append("expect must declare at least one of 'transcript' or 'latency'")
    for layer in ("transcript", "latency"):
        if layer not in expect:
            continue
        names = expect[layer]
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            errors.append(f"expect.{layer} must be a list of check-name strings")
            continue
        unknown = [n for n in names if n not in KNOWN_CHECK_NAMES[layer]]
        for n in unknown:
            errors.append(f"expect.{layer} references unknown check {n!r}")
    return errors


def _turns(call: dict) -> list[dict]:
    obj = call.get("transcript_object") or []
    return [{"role": t.get("role"), "content": t.get("content") or ""} for t in obj]


def _diff_layer(name, layer, findings, expected) -> list[FixtureMismatch]:
    actual = {f.check for f in findings}
    mismatches: list[FixtureMismatch] = []
    for check in sorted(expected - actual):
        mismatches.append(FixtureMismatch(
            name, "missing", layer, check,
            f"expected {layer} check {check!r} but it did not fire"))
    for f in findings:
        if f.check not in expected:
            mismatches.append(FixtureMismatch(name, "unexpected", layer, f.check, f.message))
    return mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_RUNS_DIR,
                        help="directory for eval run records (default: var/evals/runs)")
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES_DIR,
                        help="directory of *.json fixtures (default: evals/fixtures)")
    parser.add_argument("--advisory", action="store_true",
                        help="always exit 0 (default: gate, exit 1 on any mismatch)")
    args = parser.parse_args(argv)

    config = load_client_config(EXAMPLE_CONFIG)

    files = sorted(args.fixtures_dir.glob("*.json"))
    print(f"Fixtures dir: {args.fixtures_dir} | fixtures found: {len(files)}\n")
    if not files:
        print("No fixtures found — a gating benchmark with no fixtures is a failure.")
        return 0 if args.advisory else 1

    print(f"{'fixture':<38} {'layers':<20} verdict")
    print("-" * 78)

    all_mismatches: list[FixtureMismatch] = []
    for path in files:
        name = path.stem
        fixture_mismatches: list[FixtureMismatch] = []
        layers_run: list[str] = []

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fixture_mismatches.append(FixtureMismatch(
                name, "malformed", "-", "-", f"invalid JSON: {exc}"))
            data = None

        if data is not None:
            errors = _validate_fixture(data)
            for err in errors:
                kind = "unknown_check" if "unknown check" in err else "malformed"
                fixture_mismatches.append(FixtureMismatch(name, kind, "-", "-", err))

        if data is not None and not fixture_mismatches:
            call = data["call"]
            expect = data["expect"]
            if "transcript" in expect:
                layers_run.append("transcript")
                meta = {
                    "language": data["language"],
                    "custom_analysis_data": (call.get("call_analysis") or {}).get(
                        "custom_analysis_data") or {},
                }
                findings = tc.run_all(config, _turns(call), meta)
                fixture_mismatches += _diff_layer(
                    name, "transcript", findings, set(expect["transcript"]))
            if "latency" in expect:
                layers_run.append("latency")
                findings = lc.run_all(call)
                fixture_mismatches += _diff_layer(name, "latency", findings, set(expect["latency"]))

        verdict = "PASS" if not fixture_mismatches else "FAIL"
        print(f"{name:<38} {'+'.join(layers_run) or '-':<20} {verdict}")
        if fixture_mismatches:
            missing = sorted({m.check for m in fixture_mismatches if m.kind == "missing"})
            unexpected = [m for m in fixture_mismatches if m.kind == "unexpected"]
            structural = [m for m in fixture_mismatches if m.kind in ("malformed", "unknown_check")]
            if missing:
                print(f"    expected but did not fire: {set(missing)}")
            if unexpected:
                print("    fired but not expected:")
                for m in unexpected:
                    print(f"      - [{m.layer}] {m.check}: {m.message}")
            for m in structural:
                print(f"    {m.kind}: {m.message}")

        all_mismatches.extend(fixture_mismatches)

    print("\n" + "=" * 78)
    if not all_mismatches:
        print(f"SUMMARY: {len(files)} fixture(s), all PASS.")
    else:
        failed = len({m.fixture for m in all_mismatches})
        print(f"SUMMARY: {len(all_mismatches)} mismatch(es) across {failed} fixture(s).")

    fingerprints = {
        fingerprint_key(EXAMPLE_CONFIG, lp.code): prompt_fingerprint(compile_prompt(config, lp))
        for lp in config.languages
    }
    record = RunRecord.create(
        layer="fixtures",
        prompt_fingerprints=fingerprints,
        sources=[p.name for p in files],
        findings=all_mismatches,
    )
    record_path = write_run_record(record, args.out_dir)
    print(f"run record: {record_path}")

    return 1 if (all_mismatches and not args.advisory) else 0


if __name__ == "__main__":
    raise SystemExit(main())
