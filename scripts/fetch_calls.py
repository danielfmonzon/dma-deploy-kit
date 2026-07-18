"""Fetch recent Acme calls (transcripts + analysis) for the eval harness.

HARD-RESTRICTED to the two agent_ids recorded in
config/clients/acme-wellness.lock.json. The list request is filtered to those
agents, and every returned call's agent_id is re-checked against that allow-list
— any other agent_id is refused (never fetched or saved).

Retell endpoints (verified against docs.retellai.com):
  POST /v3/list-calls   body {"filter_criteria": {"agent": [{"agent_id": ...}]}}
  GET  /v2/get-call/{call_id}
Both use Bearer auth with RETELL_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / "config" / "clients" / "acme-wellness.lock.json"
OUT_DIR = REPO_ROOT / "capture" / "calls"
BASE_URL = "https://api.retellai.com"


def allowed_agent_ids() -> set[str]:
    lock = json.loads(LOCKFILE.read_text(encoding="utf-8"))
    ids = {entry["agent_id"] for entry in lock.values() if entry.get("agent_id")}
    if not ids:
        raise SystemExit(f"No agent_ids found in {LOCKFILE}")
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Acme calls for eval (lockfile-restricted).")
    parser.add_argument("--limit", type=int, default=50, help="max calls to list")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    import os
    key = os.environ.get("RETELL_API_KEY", "").strip()
    if not key:
        print("RETELL_API_KEY is not set.", file=sys.stderr)
        return 1

    allow = allowed_agent_ids()
    print(f"Allowed acme-wellness agent_ids ({len(allow)}): {sorted(allow)}")

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=30.0) as client:
        body = {
            "filter_criteria": {"agent": [{"agent_id": aid} for aid in sorted(allow)]},
            "limit": args.limit,
        }
        resp = client.post("/v3/list-calls", json=body)
        resp.raise_for_status()
        data = resp.json()
        # v3 returns {"items": [...], "has_more": bool}; tolerate a bare list too.
        if isinstance(data, list):
            calls = data
        else:
            calls = data.get("items") or data.get("calls") or data.get("data") or []
        print(f"list-calls returned {len(calls)} call(s)")

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        saved = 0
        for item in calls:
            call_id = item.get("call_id")
            agent_id = item.get("agent_id")
            if agent_id not in allow:
                print(f"  REFUSED (not an acme-wellness agent): {call_id} agent={agent_id}")
                continue
            full = client.get(f"/v2/get-call/{call_id}")
            full.raise_for_status()
            call = full.json()
            # defensive: re-check the fetched call's agent_id too
            if call.get("agent_id") not in allow:
                print(f"  REFUSED after fetch: {call_id} agent={call.get('agent_id')}")
                continue
            (OUT_DIR / f"{call_id}.json").write_text(
                json.dumps(call, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            saved += 1
            print(f"  saved {call_id} (agent {agent_id})")

    print(f"\nSaved {saved} call(s) to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
