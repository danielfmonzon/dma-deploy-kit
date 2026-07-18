"""Capture Retell agent configurations for a client account.

Lists every agent on the Retell account, fetches each agent's full config plus
its associated response-engine object (Retell LLM or conversation flow), and
writes the raw dumps under capture/retell/ for later inspection.

The capture/ directory is gitignored — these dumps are private client data.

Verified against Retell's API docs (https://docs.retellai.com/api-references):
  - POST https://api.retellai.com/v2/list-agents
  - GET  https://api.retellai.com/get-agent/{agent_id}
  - GET  https://api.retellai.com/get-retell-llm/{llm_id}
  - GET  https://api.retellai.com/get-conversation-flow/{conversation_flow_id}
All endpoints authenticate with an "Authorization: Bearer <RETELL_API_KEY>" header.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

API_BASE = "https://api.retellai.com"
REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "capture" / "retell"


def fail(message: str) -> None:
    """Print an error to stderr and exit non-zero. Never prints the API key."""
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def load_api_key() -> str:
    load_dotenv(REPO_ROOT / ".env")
    key = os.environ.get("RETELL_API_KEY", "").strip()
    if not key:
        fail(
            "RETELL_API_KEY is missing or empty. Fill it in in the .env file "
            "at the repo root (copy .env.example to .env if needed)."
        )
    return key


def api_get(client: httpx.Client, path: str) -> dict:
    """GET a Retell endpoint. On auth/other HTTP error, report verbatim and stop."""
    resp = client.get(f"{API_BASE}{path}")
    _raise_for_status(resp)
    return resp.json()


def api_post(client: httpx.Client, path: str, payload: dict) -> object:
    resp = client.post(f"{API_BASE}{path}", json=payload)
    _raise_for_status(resp)
    return resp.json()


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        # Report the API's response verbatim; do not retry with variations.
        fail(
            f"Retell API returned HTTP {resp.status_code} for "
            f"{resp.request.method} {resp.request.url.path}:\n{resp.text}"
        )


def fetch_response_engine(client: httpx.Client, response_engine: dict | None) -> dict | None:
    """Fetch the LLM / conversation-flow object referenced by an agent's response_engine."""
    if not response_engine:
        return None
    engine_type = response_engine.get("type")
    if engine_type == "retell-llm":
        llm_id = response_engine.get("llm_id")
        if llm_id:
            return api_get(client, f"/get-retell-llm/{llm_id}")
    elif engine_type == "conversation-flow":
        flow_id = response_engine.get("conversation_flow_id")
        if flow_id:
            return api_get(client, f"/get-conversation-flow/{flow_id}")
    # custom-llm uses a websocket URL (no fetchable object); nothing to retrieve.
    return None


def main() -> None:
    api_key = load_api_key()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(headers=headers, timeout=30.0) as client:
        agents_index = api_post(client, "/v2/list-agents", {})

        # The v2 list endpoint returns a paginated object shaped like
        # {"items": [...], "has_more": bool}. Handle a bare array or other
        # wrapper keys defensively in case the shape differs.
        if isinstance(agents_index, dict):
            agent_items = (
                agents_index.get("items")
                or agents_index.get("agents")
                or agents_index.get("data")
                or []
            )
        else:
            agent_items = agents_index

        (OUT_DIR / "_agents_index.json").write_text(
            json.dumps(agents_index, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        if not agent_items:
            print("No agents found on this Retell account.")
            print(f"Raw list saved to {OUT_DIR / '_agents_index.json'}")
            return

        print(f"Found {len(agent_items)} agent(s):\n")
        for i, item in enumerate(agent_items, start=1):
            agent_id = item.get("agent_id")
            if not agent_id:
                continue

            # Fetch the full agent config (list items carry only metadata).
            agent = api_get(client, f"/get-agent/{agent_id}")
            name = agent.get("agent_name") or item.get("agent_name") or "(unnamed)"

            response_engine = agent.get("response_engine")
            engine_detail = fetch_response_engine(client, response_engine)

            record = {
                "agent": agent,
                "response_engine_detail": engine_detail,
            }
            out_path = OUT_DIR / f"{agent_id}.json"
            out_path.write_text(
                json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            print(f"  {i}. {name}  [{agent_id}]")

        print(f"\nSaved {len(agent_items)} agent config(s) to {OUT_DIR}")
        print(f"Raw list saved to {OUT_DIR / '_agents_index.json'}")


if __name__ == "__main__":
    main()
