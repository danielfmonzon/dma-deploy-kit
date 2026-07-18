"""Layer 1 — static prompt policy checks (no network, CI-runnable).

Each check takes (config, language_code, compiled_prompt) and returns a list of
Finding objects. A finding means the compiled prompt violates a policy we can
verify statically. ``run_all`` runs every check for one language.

The greeting-language heuristic reuses the light Spanish/English detection
approach from the earlier capture-report scripts (marker characters + a small
word list) — good enough to catch a greeting authored in the wrong language.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dma_deploy_kit.agent.prompt import SECTION_ORDER
from dma_deploy_kit.config import ClientConfig

# Engine-owned marker that only appears when the medical_adjacent block is injected.
_MEDICAL_BLOCK_MARKER = "also follow these hard limits"

_SPANISH_CHARS = set("áéíóúñ¿¡üÁÉÍÓÚÑÜ")
_SPANISH_WORDS = {
    "hola", "gracias", "por", "favor", "usted", "buenos", "buenas", "dias",
    "días", "como", "cómo", "esta", "está", "español", "llamada", "cita",
    "para", "con", "que", "qué", "sí", "habla", "puedo", "ayudarle", "hoy",
}
_ENGLISH_WORDS = {
    "the", "you", "your", "and", "please", "hello", "hi", "call", "calling",
    "for", "with", "how", "can", "help", "thanks", "thank", "today", "this",
}


@dataclass(frozen=True)
class Finding:
    check: str
    language: str
    message: str


def detect_languages(text: str) -> set[str]:
    langs: set[str] = set()
    if any(ch in _SPANISH_CHARS for ch in text):
        langs.add("es")
    words = set(re.findall(r"[a-záéíóúñü]+", text.lower()))
    if words & _SPANISH_WORDS:
        langs.add("es")
    if words & _ENGLISH_WORDS:
        langs.add("en")
    return langs


def parse_sections(prompt: str) -> dict[str, str]:
    parts = re.split(r"^# (.+)$", prompt, flags=re.MULTILINE)
    out: dict[str, str] = {}
    for i in range(1, len(parts), 2):
        out[parts[i].strip()] = parts[i + 1].strip()
    return out


def _profile(config: ClientConfig, code: str):
    return next((lp for lp in config.languages if lp.code == code), None)


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def check_never_say_in_hard_rules(config, code, prompt) -> list[Finding]:
    hard = parse_sections(prompt).get("HARD RULES", "")
    return [
        Finding("never_say_missing", code, f"never_say entry not found in HARD RULES: {entry!r}")
        for entry in config.guardrails.never_say
        if entry not in hard
    ]


def check_medical_block(config, code, prompt) -> list[Finding]:
    hard = parse_sections(prompt).get("HARD RULES", "")
    present = _MEDICAL_BLOCK_MARKER in hard
    if config.guardrails.preset == "medical_adjacent" and not present:
        return [Finding("medical_block_missing", code,
                        "preset 'medical_adjacent' but medical block absent from HARD RULES")]
    if config.guardrails.preset == "none" and present:
        return [Finding("medical_block_unexpected", code,
                        "preset 'none' but engine medical block present in HARD RULES")]
    return []


def check_escalation(config, code, prompt) -> list[Finding]:
    esc = parse_sections(prompt).get("ESCALATION", "")
    findings: list[Finding] = []
    if config.escalation.contact_name not in esc:
        findings.append(Finding("escalation_contact_missing", code,
                                f"escalation contact_name not in ESCALATION: "
                                f"{config.escalation.contact_name!r}"))
    for entry in config.escalation.escalate_when:
        if entry not in esc:
            findings.append(Finding("escalate_when_missing", code,
                                    f"escalate_when entry not in ESCALATION: {entry!r}"))
    return findings


def check_greeting_language(config, code, prompt) -> list[Finding]:
    lp = _profile(config, code)
    if lp is None:
        return []
    langs = detect_languages(lp.greeting)
    low = code.lower()
    if low.startswith("es") and "es" not in langs:
        return [Finding("greeting_language", code,
                        f"es-* profile greeting lacks Spanish markers: {lp.greeting!r}")]
    if low.startswith("en") and "es" in langs:
        return [Finding("greeting_language", code,
                        f"en-* profile greeting contains Spanish markers: {lp.greeting!r}")]
    return []


def check_sample_lines(config, code, prompt) -> list[Finding]:
    samples = parse_sections(prompt).get("SAMPLE LINES", "")
    lp = _profile(config, code)
    findings: list[Finding] = []
    if lp is None:
        return findings
    for line in lp.sample_lines:
        if line not in samples:
            findings.append(Finding("sample_line_missing", code,
                                    f"configured sample line not in SAMPLE LINES: {line!r}"))
    # per-language isolation: another language's sample lines must not leak in
    for other in config.languages:
        if other.code == code:
            continue
        for line in other.sample_lines:
            if line and line in samples and line not in lp.sample_lines:
                findings.append(Finding("sample_line_leak", code,
                                        f"sample line from {other.code} present in {code} "
                                        f"SAMPLE LINES: {line!r}"))
    return findings


def check_capturing_details(config, code, prompt) -> list[Finding]:
    cap = parse_sections(prompt).get("CAPTURING DETAILS", "")
    findings: list[Finding] = []
    for field in config.post_call:
        if field.source == "caller" and field.name not in cap:
            findings.append(Finding("capture_field_missing", code,
                                    f"caller-source field not in CAPTURING DETAILS: {field.name}"))
        elif field.source == "derived" and field.name in cap:
            findings.append(Finding("derived_field_listed", code,
                                    f"derived field listed in CAPTURING DETAILS: {field.name}"))
    return findings


def check_structure(config, code, prompt) -> list[Finding]:
    findings: list[Finding] = []
    if "{" in prompt or "}" in prompt:
        findings.append(Finding("placeholder_artifact", code,
                                "compiled prompt contains '{' or '}' placeholder artifact"))
    headings = re.findall(r"^# (.+)$", prompt, flags=re.MULTILINE)
    if headings != SECTION_ORDER:
        findings.append(Finding("heading_mismatch", code,
                                f"headings/order mismatch: {headings}"))
    return findings


ALL_CHECKS = [
    check_never_say_in_hard_rules,
    check_medical_block,
    check_escalation,
    check_greeting_language,
    check_sample_lines,
    check_capturing_details,
    check_structure,
]


def run_all(config: ClientConfig, code: str, prompt: str) -> list[Finding]:
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        findings.extend(check(config, code, prompt))
    return findings
