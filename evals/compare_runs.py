"""Regression compare CLI over eval run records.

A pure reader over the JSON manifests written by runlog.write_run_record. It
diffs two records of the SAME layer and answers one question: did any check
newly fire in the candidate versus the baseline? Prompt fingerprints may change
freely — that is the point of pinning them — so a fingerprint difference with no
new finding is NOT a regression. Only a new finding is.

Two invocation forms (mutually exclusive):

    python evals/compare_runs.py <baseline.json> <candidate.json>
    python evals/compare_runs.py --layer <static|transcript|latency|fixtures> --latest
        [--runs-dir DIR]

Exit codes: 0 = OK (no new findings), 1 = REGRESSION (>=1 new finding),
2 = usage/selection error (bad args, cross-layer compare, <2 records).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from runlog import DEFAULT_RUNS_DIR, RunRecord, read_run_record

LAYERS = ["static", "transcript", "latency", "fixtures", "judge"]


def _canonical(finding: dict) -> tuple:
    """Hashable canonical form of a serialized finding (flat scalar dict)."""
    return tuple(sorted(finding.items()))


def _finding_set(record: RunRecord) -> set[tuple]:
    return {_canonical(f) for f in record.findings}


def _err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


def _select_latest(runs_dir: Path, layer: str) -> tuple[RunRecord, RunRecord] | int:
    """Return (baseline, candidate) = the two most recent records of ``layer``.

    Candidate is the newest; baseline the second-newest. Sort by created_at then
    run_id. Returns exit code 2 if fewer than two matching records exist.
    """
    records: list[RunRecord] = []
    for path in sorted(runs_dir.glob("*.json")):
        try:
            rec = read_run_record(path)
        except (ValueError, OSError, KeyError):
            continue  # skip anything that isn't a readable v1 record
        if rec.layer == layer:
            records.append(rec)
    if len(records) < 2:
        return _err(
            f"need >=2 '{layer}' records in {runs_dir} to compare with --latest, "
            f"found {len(records)}"
        )
    records.sort(key=lambda r: (r.created_at, r.run_id))
    return records[-2], records[-1]


def _print_header(layer: str, baseline: RunRecord, candidate: RunRecord) -> None:
    print("=== eval run comparison ===")
    print(f"layer:     {layer}")
    print(f"baseline:  {baseline.run_id}  git {baseline.git_commit}  {baseline.created_at}")
    print(f"candidate: {candidate.run_id}  git {candidate.git_commit}  {candidate.created_at}")


def _print_prompts(baseline: RunRecord, candidate: RunRecord) -> None:
    base_fp = baseline.prompt_fingerprints
    cand_fp = candidate.prompt_fingerprints
    keys = sorted(set(base_fp) | set(cand_fp))
    changed = 0
    print("\nPROMPTS")
    for key in keys:
        if key in base_fp and key in cand_fp:
            if base_fp[key] == cand_fp[key]:
                status = "unchanged"
            else:
                status = "CHANGED"
                changed += 1
        elif key in base_fp:
            status = "only-in-baseline"
        else:
            status = "only-in-candidate"
        print(f"  {status:<18} {key}")
    print(f"  {len(keys)} prompts compared, {changed} changed.")


def _print_findings(new: set[tuple], resolved: set[tuple], persisting: set[tuple]) -> None:
    print("\nFINDINGS")
    if new:
        print(f"  new ({len(new)}):")
        for canon in sorted(new):
            d = dict(canon)
            print(f"    NEW  {d.get('check', '?')}")
            for k, v in sorted(d.items()):
                print(f"           {k}: {v!r}")
    else:
        print("  new (0): none")
    if resolved:
        print(f"  resolved ({len(resolved)}):")
        for canon in sorted(resolved):
            d = dict(canon)
            print(f"    resolved: {d.get('check', '?')} — {d.get('message', '')}")
    if persisting:
        counts = Counter(dict(c).get("check", "?") for c in persisting)
        breakdown = ", ".join(f"{name}×{n}" for name, n in sorted(counts.items()))
        print(f"  persisting ({len(persisting)}): {breakdown}")


def _compare(layer: str, baseline: RunRecord, candidate: RunRecord) -> int:
    _print_header(layer, baseline, candidate)
    _print_prompts(baseline, candidate)

    base_findings = _finding_set(baseline)
    cand_findings = _finding_set(candidate)
    new = cand_findings - base_findings
    resolved = base_findings - cand_findings
    persisting = cand_findings & base_findings
    _print_findings(new, resolved, persisting)

    # Honest caveats before the verdict.
    prompts_changed = baseline.prompt_fingerprints != candidate.prompt_fingerprints
    if set(baseline.sources) != set(candidate.sources):
        print("\nNOTE: sources differ between runs; finding delta may reflect data, not prompts.")
    if prompts_changed and not new and not resolved and persisting:
        print("\nNOTE: prompts changed but the finding set is identical — "
              "no new or resolved checks.")

    print()
    if new:
        print(f"VERDICT: REGRESSION — {len(new)} new finding(s)")
        return 1
    print(f"VERDICT: OK — no new findings ({len(resolved)} resolved, {len(persisting)} persisting)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two eval run records for regressions.")
    parser.add_argument("baseline", nargs="?", help="baseline run-record JSON path")
    parser.add_argument("candidate", nargs="?", help="candidate run-record JSON path")
    parser.add_argument("--layer", choices=LAYERS, help="layer to select with --latest")
    parser.add_argument("--latest", action="store_true",
                        help="compare the two most recent records of --layer")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR,
                        help="records directory for --latest (default: var/evals/runs)")
    args = parser.parse_args(argv)

    explicit = args.baseline is not None and args.candidate is not None
    latest = args.latest

    if explicit and latest:
        return _err("choose either two explicit paths OR --latest, not both")
    if latest:
        if not args.layer:
            return _err("--latest requires --layer")
        if args.baseline is not None:
            return _err("--latest takes no positional paths")
        selected = _select_latest(args.runs_dir, args.layer)
        if isinstance(selected, int):
            return selected
        baseline, candidate = selected
        return _compare(args.layer, baseline, candidate)
    if explicit:
        if args.layer or args.latest:
            return _err("explicit paths do not take --layer/--latest")
        try:
            baseline = read_run_record(args.baseline)
            candidate = read_run_record(args.candidate)
        except (ValueError, OSError) as exc:
            return _err(f"could not read record: {exc}")
        if baseline.layer != candidate.layer:
            return _err(
                f"cannot compare across layers: baseline is '{baseline.layer}', "
                f"candidate is '{candidate.layer}'"
            )
        return _compare(baseline.layer, baseline, candidate)

    return _err("provide two record paths, or --layer <L> --latest")


if __name__ == "__main__":
    raise SystemExit(main())
