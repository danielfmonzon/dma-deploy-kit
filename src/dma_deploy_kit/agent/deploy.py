"""Deploy engine: build desired Retell state, diff against live, plan, and apply.

This module is deploy-safe by construction for planning: ``plan`` only ever issues
read-only GET calls (and only when a lockfile entry already exists). ``apply``
performs the create/update mutations and is intentionally NOT invoked during the
dry-run step.

Retell endpoints (verified against docs.retellai.com):
  POST  /create-agent
  PATCH /update-agent/{agent_id}
  GET   /get-agent/{agent_id}
  POST  /create-retell-llm
  PATCH /update-retell-llm/{llm_id}
  GET   /get-retell-llm/{llm_id}
All authenticate with "Authorization: Bearer <RETELL_API_KEY>".
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from ..config.models import ClientConfig
from .constants import AGENT_DEFAULTS, KB_CONFIG_TUNING, LLM_DEFAULTS
from .prompt import compile_prompt

BASE_URL = "https://api.retellai.com"
REPO_ROOT = Path(__file__).resolve().parents[3]


class DeployError(RuntimeError):
    """Raised for deploy/config/transport problems."""


# --------------------------------------------------------------------------- #
# Retell HTTP client
# --------------------------------------------------------------------------- #
class RetellClient:
    """Thin httpx wrapper over the Retell agent / retell-llm endpoints."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        key = (api_key if api_key is not None else os.environ.get("RETELL_API_KEY", "")).strip()
        if not key and transport is None:
            raise DeployError(
                "RETELL_API_KEY is missing or empty. Set it in .env before making "
                "live Retell calls."
            )
        headers = {"Authorization": f"Bearer {key or 'test'}", "Content-Type": "application/json"}
        self._client = httpx.Client(
            base_url=base_url, headers=headers, timeout=timeout, transport=transport
        )

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        resp = self._client.request(method, path, json=payload)
        if resp.status_code >= 400:
            raise DeployError(f"Retell {method} {path} -> HTTP {resp.status_code}: {resp.text}")
        return resp.json()

    def get_agent(self, agent_id: str) -> dict:
        return self._request("GET", f"/get-agent/{agent_id}")

    def create_agent(self, payload: dict) -> dict:
        return self._request("POST", "/create-agent", payload)

    def update_agent(self, agent_id: str, payload: dict) -> dict:
        return self._request("PATCH", f"/update-agent/{agent_id}", payload)

    def get_retell_llm(self, llm_id: str) -> dict:
        return self._request("GET", f"/get-retell-llm/{llm_id}")

    def create_retell_llm(self, payload: dict) -> dict:
        return self._request("POST", "/create-retell-llm", payload)

    def update_retell_llm(self, llm_id: str, payload: dict) -> dict:
        return self._request("PATCH", f"/update-retell-llm/{llm_id}", payload)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RetellClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# desired state
# --------------------------------------------------------------------------- #
def _post_call_analysis_data(config: ClientConfig) -> list[dict]:
    """Map config.post_call to Retell's post_call_analysis_data shape.

    Retell AnalysisData items are {type, name, description}, with an additional
    non-empty `choices` list for enums. boolean/number/string need no extra
    fields. Our per-field `source` (caller/derived) is a kit-side concept and is
    not part of the Retell payload.
    """
    out: list[dict] = []
    for field in config.post_call:
        entry = {"type": field.type, "name": field.name, "description": field.description}
        if field.type == "enum":
            entry["choices"] = list(field.choices or [])
        out.append(entry)
    return out


def build_desired_state(config: ClientConfig) -> list[dict]:
    """Build the desired agent + retell-llm payload for each language profile.

    Returns a list of dicts, one per language, each shaped:
        {"code", "agent_name", "agent": <managed agent fields>, "llm": <llm fields>}

    The agent payload holds only fields the kit manages (no response_engine — its
    llm_id is wired in during apply — and deliberately no webhook_url yet).
    """
    codes = [lp.code for lp in config.languages]
    # Match production: a multi-language client lists all codes; a single-language
    # client uses the bare string (as the captured single-language agent did).
    agent_language: object = codes[0] if len(codes) == 1 else codes

    states: list[dict] = []
    for lp in config.languages:
        agent_name = f"{config.client.business_name} — {lp.code}"
        agent_payload: dict = {
            "agent_name": agent_name,
            "voice_id": lp.voice_id,
            "language": agent_language,
            **AGENT_DEFAULTS,
            "max_call_duration_ms": config.agent.max_call_duration_ms,
            "enable_expressive_mode": config.agent.enable_expressive_mode,
            "expressive_emotion_tags": list(config.agent.expressive_emotion_tags),
            "pronunciation_dictionary": [p.model_dump() for p in config.agent.pronunciation],
            # post_call_analysis_model comes from AGENT_DEFAULTS above.
            "post_call_analysis_data": _post_call_analysis_data(config),
        }
        if config.agent.ambient_sound is not None:
            agent_payload["ambient_sound"] = config.agent.ambient_sound

        llm_payload: dict = {
            **LLM_DEFAULTS,
            "general_prompt": compile_prompt(config, lp),
            "begin_message": lp.greeting,
        }
        if config.agent.knowledge_base_ids:
            llm_payload["knowledge_base_ids"] = list(config.agent.knowledge_base_ids)
            llm_payload["kb_config"] = dict(KB_CONFIG_TUNING)

        states.append(
            {"code": lp.code, "agent_name": agent_name, "agent": agent_payload, "llm": llm_payload}
        )
    return states


# --------------------------------------------------------------------------- #
# lockfile
# --------------------------------------------------------------------------- #
def lockfile_path(config: ClientConfig) -> Path:
    return REPO_ROOT / "config" / "clients" / f"{config.client.slug}.lock.json"


def read_lockfile(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeployError(f"{path}: cannot read lockfile: {exc}") from exc


def write_lockfile(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# plan / apply
# --------------------------------------------------------------------------- #
def _diff(desired: dict, live: dict) -> dict:
    """Field-level diff of desired vs live, restricted to fields we manage."""
    changes: dict = {}
    for key, want in desired.items():
        have = live.get(key, "<<absent>>")
        if have != want:
            changes[key] = {"desired": want, "live": have}
    return changes


def plan(
    config: ClientConfig,
    client: RetellClient | None = None,
    lockfile: Path | None = None,
) -> dict:
    """Compute a per-language plan. Read-only: fetches live state only for UPDATE."""
    path = lockfile if lockfile is not None else lockfile_path(config)
    lock = read_lockfile(path)
    states = build_desired_state(config)

    items: list[dict] = []
    for st in states:
        code = st["code"]
        entry = lock.get(code)
        if not entry:
            items.append(
                {
                    "code": code,
                    "action": "CREATE",
                    "agent_name": st["agent_name"],
                    "agent": st["agent"],
                    "llm": st["llm"],
                }
            )
            continue
        if client is None:
            raise DeployError(
                f"lockfile has an entry for '{code}' but no Retell client was provided "
                "to fetch live state for the diff."
            )
        live_agent = client.get_agent(entry["agent_id"])
        live_llm = client.get_retell_llm(entry["llm_id"])
        agent_diff = _diff(st["agent"], live_agent)
        llm_diff = _diff(st["llm"], live_llm)
        action = "NOOP" if not agent_diff and not llm_diff else "UPDATE"
        items.append(
            {
                "code": code,
                "action": action,
                "agent_name": st["agent_name"],
                "ids": dict(entry),
                "agent_diff": agent_diff,
                "llm_diff": llm_diff,
            }
        )
    return {"slug": config.client.slug, "items": items}


def apply(
    config: ClientConfig,
    plan_result: dict,
    client: RetellClient,
    lockfile: Path | None = None,
) -> dict:
    """Execute a plan (mutations!) and update the lockfile. NOT called in dry-run."""
    path = lockfile if lockfile is not None else lockfile_path(config)
    lock = read_lockfile(path)
    states = {st["code"]: st for st in build_desired_state(config)}

    for item in plan_result["items"]:
        code = item["code"]
        action = item["action"]
        if action == "CREATE":
            st = states[code]
            llm = client.create_retell_llm(st["llm"])
            llm_id = llm["llm_id"]
            agent_body = {
                **st["agent"],
                "response_engine": {"type": "retell-llm", "llm_id": llm_id},
            }
            agent = client.create_agent(agent_body)
            lock[code] = {"agent_id": agent["agent_id"], "llm_id": llm_id}
        elif action == "UPDATE":
            ids = item["ids"]
            if item.get("llm_diff"):
                client.update_retell_llm(
                    ids["llm_id"], {k: v["desired"] for k, v in item["llm_diff"].items()}
                )
            if item.get("agent_diff"):
                client.update_agent(
                    ids["agent_id"], {k: v["desired"] for k, v in item["agent_diff"].items()}
                )
            lock[code] = dict(ids)
        # NOOP: nothing to do

    write_lockfile(path, lock)
    return lock
