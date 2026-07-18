"""Run Layer 1 static prompt checks against local configs; exit nonzero on findings.

Always checks config/client.example.yaml, plus any config/clients/*.yaml present
locally (those are gitignored, so CI only sees the example — that is expected).
"""

from __future__ import annotations

from pathlib import Path

from static_checks import run_all

from dma_deploy_kit.agent.prompt import compile_prompt
from dma_deploy_kit.config import load_client_config

REPO_ROOT = Path(__file__).resolve().parent.parent


def iter_config_paths():
    yield REPO_ROOT / "config" / "client.example.yaml"
    clients = REPO_ROOT / "config" / "clients"
    if clients.exists():
        yield from sorted(clients.glob("*.yaml"))


def main() -> int:
    total = 0
    for path in iter_config_paths():
        config = load_client_config(path)
        rel = path.relative_to(REPO_ROOT)
        print(f"== {rel} ({config.client.slug}) ==")
        for lp in config.languages:
            prompt = compile_prompt(config, lp)
            findings = run_all(config, lp.code, prompt)
            if not findings:
                print(f"  [{lp.code}] OK ({len(prompt)} chars)")
            for finding in findings:
                print(f"  [{finding.language}] FAIL {finding.check}: {finding.message}")
                total += 1
    print(f"\n{total} finding(s).")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
