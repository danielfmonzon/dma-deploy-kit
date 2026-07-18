"""Layer 2 — deterministic transcript checks (no LLM, no network).

Each check takes (config, transcript_turns, call_metadata) and returns findings.
  * transcript_turns: list of {"role": "agent"|"user"|..., "content": str}
  * call_metadata: {"agent_id", "language" (expected code), "custom_analysis_data" (dict)}

All checks are deterministic regex / string / lookup — no model judgment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from static_checks import detect_languages

from dma_deploy_kit.config import ClientConfig
from dma_deploy_kit.postcall import consent_field_name

# --- human-claim patterns (word-boundary regex family) ----------------------
# Matches an agent asserting it is a human / real person / not an AI. Kept narrow
# to avoid flagging benign mentions of the words "person"/"human".
HUMAN_CLAIM_PATTERNS = [
    r"\bi am (?:a |an )?human\b",
    r"\bi'?m (?:a |an )?human\b",
    r"\bi am (?:a |an )?real (?:person|human|human being)\b",
    r"\bi'?m (?:a |an )?real (?:person|human|human being)\b",
    r"\bi am (?:a |an )?live (?:person|agent|human)\b",
    r"\bi'?m (?:a |an )?live (?:person|agent|human)\b",
    r"\bi am not (?:a |an )?(?:bot|ai|a\.i\.|robot|machine|computer|virtual assistant)\b",
    r"\bi'?m not (?:a |an )?(?:bot|ai|a\.i\.|robot|machine|computer|virtual assistant)\b",
    r"\byes,? i am (?:a )?(?:real )?(?:person|human)\b",
    r"\byes,? i'?m (?:a )?(?:real )?(?:person|human)\b",
    r"\bspeaking (?:with|to) a real (?:person|human)\b",
]

# --- SMS-promise phrases -----------------------------------------------------
SMS_PROMISE_PATTERNS = [
    r"\btext you\b",
    r"\bi'?ll text\b",
    r"\bi will text\b",
    r"\bsend you (?:a |the )?(?:text|sms|link)\b",
    r"\btext (?:you )?(?:the|a) (?:link|booking)\b",
    r"\bshoot you a text\b",
    r"\bsend (?:it|the link|the booking link) (?:by|via) text\b",
]

# never_say entries starting with any of these are policy instructions, not
# quotable phrases (see forbidden_tokens for how tokens are derived from them).
_INSTRUCTION_PREFIXES = ("never", "do not", "don't", "dont", "avoid", "no ", "refrain")


@dataclass(frozen=True)
class TranscriptFinding:
    check: str
    message: str
    turn_index: int | None = None
    quote: str | None = None


def _agent_turns(turns: list[dict]):
    for i, turn in enumerate(turns):
        if turn.get("role") == "agent":
            yield i, (turn.get("content") or "")


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "y")
    return bool(value)


def forbidden_tokens(config: ClientConfig) -> list[str]:
    """Derive quotable forbidden tokens from guardrails.never_say.

    Derivation rule (documented):
      * A never_say entry that does NOT begin with an instruction verb
        (never/do not/don't/avoid/no/refrain) is itself a quotable phrase and is
        matched whole.
      * An entry that DOES begin with an instruction verb is a policy instruction,
        not a quotable string, so we do not match it whole. Instead we extract the
        hyphenated claim tokens inside it (e.g. "FDA-approved", "post-procedure"),
        which are almost always the concrete forbidden claim.
    Non-hyphenated claim words embedded in instructions (e.g. "guaranteed") are
    intentionally NOT auto-derived — a client wanting those string-matched should
    add them as their own short quotable never_say entries.
    """
    tokens: set[str] = set()
    for entry in config.guardrails.never_say:
        stripped = entry.strip()
        if stripped.lower().startswith(_INSTRUCTION_PREFIXES):
            tokens.update(re.findall(r"\b[A-Za-z]+-[A-Za-z]+\b", stripped))
        elif stripped:
            tokens.add(stripped)
    return sorted(tokens)


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def check_human_claim(config, turns, meta) -> list[TranscriptFinding]:
    findings: list[TranscriptFinding] = []
    for i, content in _agent_turns(turns):
        for pattern in HUMAN_CLAIM_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                findings.append(TranscriptFinding(
                    "human_claim", "agent turn claims to be a human/real person", i, content))
                break
    return findings


def check_sms_promise_without_consent(config, turns, meta) -> list[TranscriptFinding]:
    consent_field = consent_field_name(config)
    custom = meta.get("custom_analysis_data") or {}
    consent_value = custom.get(consent_field) if consent_field else None
    if _is_truthy(consent_value):
        return []  # consent captured -> texting promises are allowed
    findings: list[TranscriptFinding] = []
    for i, content in _agent_turns(turns):
        for pattern in SMS_PROMISE_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                findings.append(TranscriptFinding(
                    "sms_promise_no_consent",
                    "agent promised a text but consent field is falsy/absent", i, content))
                break
    return findings


def check_forbidden_phrases(config, turns, meta) -> list[TranscriptFinding]:
    tokens = forbidden_tokens(config)
    findings: list[TranscriptFinding] = []
    for i, content in _agent_turns(turns):
        for token in tokens:
            if re.search(rf"\b{re.escape(token)}\b", content, re.IGNORECASE):
                findings.append(TranscriptFinding(
                    "forbidden_phrase", f"agent said forbidden token {token!r}", i, content))
    return findings


def check_greeting_language(config, turns, meta) -> list[TranscriptFinding]:
    expected = (meta.get("language") or "").lower()
    first = next((c for _, c in _agent_turns(turns)), None)
    if not expected or not first:
        return []
    langs = detect_languages(first)
    if expected.startswith("es") and "es" not in langs:
        return [TranscriptFinding(
            "greeting_language", "first agent turn is not Spanish for an es-* agent", 0, first)]
    if expected.startswith("en") and "es" in langs and "en" not in langs:
        return [TranscriptFinding(
            "greeting_language", "first agent turn appears Spanish for an en-* agent", 0, first)]
    return []


ALL_CHECKS = [
    check_human_claim,
    check_sms_promise_without_consent,
    check_forbidden_phrases,
    check_greeting_language,
]


def run_all(config: ClientConfig, turns: list[dict], meta: dict) -> list[TranscriptFinding]:
    findings: list[TranscriptFinding] = []
    for check in ALL_CHECKS:
        findings.extend(check(config, turns, meta))
    return findings
