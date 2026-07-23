"""Run Layer 1 static prompt checks against local configs; exit nonzero on findings.

Always checks config/client.example.yaml, plus any config/clients/*.yaml present
locally (those are gitignored, so CI only sees the example — that is expected).

Writes an eval run record (see runlog.py) capturing the prompt fingerprints,
sources, and findings for this run.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from runlog import DEFAULT_RUNS_DIR, RunRecord, prompt_fingerprint, write_run_record
from static_checks import run_all

from dma_deploy_kit.agent.prompt import compile_prompt
from dma_deploy_kit.config import load_client_config

REPO_ROOT = Path(__file__).resolve().parent.parent


def iter_config_paths():
    yield REPO_ROOT / "config" / "client.example.yaml"
    clients = REPO_ROOT / "config" / "clients"
    if clients.exists():
        yield from sorted(clients.glob("*.yaml"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help="directory for eval run records (default: var/evals/runs)",
    )
    args = parser.parse_args(argv)

    total = 0
    fingerprints: dict[str, str] = {}
    sources: list[str] = []
    all_findings: list = []
    for path in iter_config_paths():
        config = load_client_config(path)
        rel = path.relative_to(REPO_ROOT)
        sources.append(str(rel))
        print(f"== {rel} ({config.client.slug}) ==")
        for lp in config.languages:
            prompt = compile_prompt(config, lp)
            fingerprints[f"{config.client.slug}/{lp.code}"] = prompt_fingerprint(prompt)
            findings = run_all(config, lp.code, prompt)
            if not findings:
                print(f"  [{lp.code}] OK ({len(prompt)} chars)")
            for finding in findings:
                print(f"  [{finding.language}] FAIL {finding.check}: {finding.message}")
                total += 1
            all_findings.extend(findings)
    print(f"\n{total} finding(s).")

    record = RunRecord.create(
        layer="static",
        prompt_fingerprints=fingerprints,
        sources=sources,
        findings=all_findings,
    )
    record_path = write_run_record(record, args.out_dir)
    print(f"run record: {record_path}")

    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
