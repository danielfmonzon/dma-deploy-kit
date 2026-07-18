"""Engine-owned Retell settings, extracted from the captured production agents.

These are the agent- and response-engine-level fields whose values were
IDENTICAL across all three captured production agents (capture/retell/*.json).
They are plain configuration values — no client data — so they live in code as
the kit's engine defaults and are applied to every deployed agent.

Extraction is documented in scripts / the field report; the literals below were
lifted verbatim from the captured configs.

DTMF NOTE: production has ``allow_user_dtmf = True`` (see AGENT_DEFAULTS). Ruling:
keep DTMF enabled to match production, and align the prompt — the SPEAKING RULES
section now tells the agent that keyed digits may arrive as typed input and to read
them back to confirm, rather than claiming it cannot detect keypresses.
"""

from __future__ import annotations

# --- agent-level defaults (identical across all three captured agents) -------
AGENT_DEFAULTS: dict = {
    "channel": "voice",
    "allow_user_dtmf": True,  # production value; prompt aligned (SPEAKING RULES reads digits back)
    "user_dtmf_options": {},
    "interruption_sensitivity": 0.9,
    "voice_speed": 1,
    "voice_temperature": 1,
    "volume": 1,
    "webhook_events": ["call_analyzed"],
    "pii_config": {"categories": [], "mode": "post_call"},
    "data_storage_setting": "everything",
    "handbook_config": {"ai_disclosure": True, "default_personality": True},
    "post_call_analysis_model": "gpt-4.1",
    "opt_in_signed_url": False,
}

# --- response-engine (Retell LLM) defaults (identical across all three) -------
LLM_MODEL = "claude-4.6-sonnet"
LLM_MODEL_HIGH_PRIORITY = False
LLM_TOOL_CALL_STRICT_MODE = True
LLM_START_SPEAKER = "agent"

# The single end_call tool present on every production agent, verbatim.
LLM_GENERAL_TOOLS: list[dict] = [
    {
        "type": "end_call",
        "name": "end_call",
        "description": (
            "End the call when user has to leave (like says bye) or you are "
            "instructed to do so."
        ),
        "speak_after_execution": True,
    }
]

# Knowledge-base retrieval tuning (filter_score/top_k identical across all three;
# kb_instruction and knowledge_base_ids are per-client and handled elsewhere).
KB_CONFIG_TUNING: dict = {"filter_score": 0.6, "top_k": 3}

LLM_DEFAULTS: dict = {
    "model": LLM_MODEL,
    "model_high_priority": LLM_MODEL_HIGH_PRIORITY,
    "tool_call_strict_mode": LLM_TOOL_CALL_STRICT_MODE,
    "start_speaker": LLM_START_SPEAKER,
    "general_tools": LLM_GENERAL_TOOLS,
}
