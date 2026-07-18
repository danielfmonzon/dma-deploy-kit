"""Plan (and optionally apply) a client's Retell deployment.

Usage:
    python scripts/deploy_client.py <config.yaml>            # dry-run: print the plan
    python scripts/deploy_client.py <config.yaml> --apply    # execute (mutations!)

The dry-run default is read-only: it fetches live agent/llm state only for
languages that already exist in the lockfile, and issues no mutation calls.

Plan output summarizes prompts and greetings as character counts rather than
printing their text, so the output is safe to share (no secrets, no prompt text).
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from dma_deploy_kit.agent.deploy import DeployError, RetellClient, apply, plan
from dma_deploy_kit.config import ClientConfigError, load_client_config


def _fmt_agent(agent: dict) -> list[str]:
    tags = agent.get("expressive_emotion_tags") or []
    pron = agent.get("pronunciation_dictionary") or []
    return [
        f"    agent_name: {agent['agent_name']}",
        f"    voice_id: {agent['voice_id']}  |  language: {agent['language']}",
        f"    max_call_duration_ms: {agent['max_call_duration_ms']}  |  "
        f"allow_user_dtmf: {agent['allow_user_dtmf']}  |  "
        f"interruption_sensitivity: {agent['interruption_sensitivity']}",
        f"    expressive_mode: {agent['enable_expressive_mode']}  |  tags: {tags}  |  "
        f"ambient_sound: {agent.get('ambient_sound')}  |  pronunciation entries: {len(pron)}",
        "    webhook_url: (none — post-call service added later)",
        f"    total managed agent fields: {len(agent)}",
    ]


def _fmt_llm(llm: dict) -> list[str]:
    tools = [t.get("name") for t in llm.get("general_tools", [])]
    return [
        f"    llm.model: {llm['model']}  |  start_speaker: {llm['start_speaker']}  |  "
        f"tools: {tools}",
        f"    general_prompt: {len(llm['general_prompt'])} chars  |  "
        f"begin_message: {len(llm['begin_message'])} chars",
        f"    knowledge_base_ids: {llm.get('knowledge_base_ids', [])}",
    ]


def _fmt_diff(label: str, diff: dict) -> list[str]:
    if not diff:
        return []
    lines = [f"    {label} changes ({len(diff)}):"]
    for key, change in diff.items():
        want, have = change["desired"], change["live"]
        if isinstance(want, str) and len(want) > 60:
            lines.append(f"      - {key}: text changed ({len(str(have))} -> {len(want)} chars)")
        else:
            lines.append(f"      - {key}: {have!r} -> {want!r}")
    return lines


def format_plan(plan_result: dict) -> str:
    lines = [f"Deployment plan for client '{plan_result['slug']}':", ""]
    for item in plan_result["items"]:
        lines.append(f"[{item['action']}] {item['agent_name']}  ({item['code']})")
        if item["action"] == "CREATE":
            lines += _fmt_agent(item["agent"])
            lines += _fmt_llm(item["llm"])
        elif item["action"] == "UPDATE":
            lines.append(f"    ids: {item['ids']}")
            lines += _fmt_diff("agent", item["agent_diff"])
            lines += _fmt_diff("llm", item["llm_diff"])
        else:  # NOOP
            lines.append("    no changes — live state matches desired")
        lines.append("")
    actions = [i["action"] for i in plan_result["items"]]
    counts = [f"{actions.count(a)} {a}" for a in ("CREATE", "UPDATE", "NOOP") if a in actions]
    lines.append(f"Summary: {', '.join(counts) or 'nothing to do'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or apply a Retell deployment.")
    parser.add_argument("config", help="Path to a client config YAML file.")
    parser.add_argument("--apply", action="store_true", help="Execute the plan (mutations!).")
    args = parser.parse_args(argv)
    load_dotenv()  # pick up RETELL_API_KEY from .env when present

    try:
        config = load_client_config(args.config)
    except ClientConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # A client is only needed to fetch live state (UPDATE) or to apply. For a
    # first-time dry-run (no lockfile) planning is fully offline.
    client: RetellClient | None = None
    try:
        client = RetellClient()
    except DeployError:
        client = None  # fine for CREATE-only dry-runs

    try:
        plan_result = plan(config, client=client)
    except DeployError as exc:
        print(f"Planning failed: {exc}", file=sys.stderr)
        return 1

    print(format_plan(plan_result))

    if args.apply:
        if client is None:
            print("Cannot --apply without a Retell client (set RETELL_API_KEY).", file=sys.stderr)
            return 1
        print("\nApplying...")
        lock = apply(config, plan_result, client)
        print(f"Applied. Lockfile now tracks: {sorted(lock)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
