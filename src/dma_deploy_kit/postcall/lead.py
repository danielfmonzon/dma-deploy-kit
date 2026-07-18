"""Parse Retell call_analyzed webhooks into Leads, resolved to the owning client.

Agent ownership is resolved by scanning ``config/clients/*.lock.json`` (written by
the deploy engine): each lockfile maps language code -> {agent_id, llm_id} for one
client slug. An agent_id we don't find there is not ours — the caller should
acknowledge and skip it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from ..config import ClientConfig, load_client_config

REPO_ROOT = Path(__file__).resolve().parents[3]
CLIENTS_DIR = REPO_ROOT / "config" / "clients"


@dataclass(frozen=True)
class AgentBinding:
    """Which client + language an agent_id belongs to, with the client's config."""

    slug: str
    language: str
    config: ClientConfig


class AgentRegistry:
    """agent_id -> AgentBinding, built from the deploy lockfiles at startup."""

    def __init__(self, bindings: dict[str, AgentBinding]) -> None:
        self._by_agent = dict(bindings)

    def __len__(self) -> int:
        return len(self._by_agent)

    def resolve(self, agent_id: str | None) -> AgentBinding | None:
        if not agent_id:
            return None
        return self._by_agent.get(agent_id)

    @classmethod
    def from_clients_dir(cls, clients_dir: Path = CLIENTS_DIR) -> AgentRegistry:
        bindings: dict[str, AgentBinding] = {}
        if not clients_dir.exists():
            return cls(bindings)
        for lock_path in sorted(clients_dir.glob("*.lock.json")):
            slug = lock_path.name[: -len(".lock.json")]
            config_path = clients_dir / f"{slug}.yaml"
            if not config_path.exists():
                continue
            config = load_client_config(config_path)
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            for code, ids in lock.items():
                agent_id = (ids or {}).get("agent_id")
                if agent_id:
                    bindings[agent_id] = AgentBinding(slug=slug, language=code, config=config)
        return cls(bindings)


class Lead(BaseModel):
    """A structured lead extracted from a completed, analyzed call."""

    slug: str
    business_name: str
    language: str
    call_id: str
    agent_id: str
    from_number: str | None = None
    to_number: str | None = None
    duration_ms: int | None = None
    start_timestamp: int | None = None
    end_timestamp: int | None = None
    disconnection_reason: str | None = None
    # The client's configured post_call fields, pulled from custom_analysis_data.
    fields: dict[str, object] = {}


def parse_lead(payload: dict, binding: AgentBinding) -> Lead:
    """Build a Lead from a call_analyzed payload using the client's post_call schema."""
    call = payload.get("call") or {}
    analysis = call.get("call_analysis") or {}
    custom = analysis.get("custom_analysis_data") or {}

    # Only surface the fields this client actually configured (in schema order).
    # For a derived field literally named "call_summary" that Retell didn't populate
    # in custom_analysis_data, fall back to the preset call_analysis.call_summary.
    # We do NOT guess preset mappings for any other derived field.
    fields: dict[str, object] = {}
    for field in binding.config.post_call:
        value = custom.get(field.name)
        if (
            field.source == "derived"
            and field.name == "call_summary"
            and value in (None, "")
        ):
            value = analysis.get("call_summary")
        fields[field.name] = value

    start = call.get("start_timestamp")
    end = call.get("end_timestamp")
    duration = call.get("duration_ms")
    if duration is None and isinstance(start, int) and isinstance(end, int):
        duration = end - start

    return Lead(
        slug=binding.slug,
        business_name=binding.config.client.business_name,
        language=binding.language,
        call_id=str(call.get("call_id", "")),
        agent_id=str(call.get("agent_id", "")),
        from_number=call.get("from_number"),
        to_number=call.get("to_number"),
        duration_ms=duration,
        start_timestamp=start,
        end_timestamp=end,
        disconnection_reason=call.get("disconnection_reason"),
        fields=fields,
    )
