"""Deterministic prompt compiler for dma-deploy-kit.

``compile_prompt`` turns a validated :class:`ClientConfig` plus one
:class:`LanguageProfile` into a Retell ``general_prompt`` string, following a
fixed 13-section taxonomy modeled on the production DMA prompts. The *taxonomy*
(section names, order, ``# HEADING`` style) is what we reuse; every body here is
original engine text — no client prose is copied.

Design invariants:
  * Pure function: no I/O, no Jinja, no globals mutated, no randomness.
  * The greeting (begin_message) is a separate Retell field and is intentionally
    NOT part of the prompt, so the only per-language content is the LANGUAGE
    section. All other sections are business-level and identical across the
    client's languages — mirroring the captured production pattern where EN and
    ES prompts differed only in LANGUAGE (plus minor flow wording).
  * Engine-owned constants (speaking rules, style, guardrail presets, …) live in
    code, not YAML.
"""

from __future__ import annotations

from ..config.models import ClientConfig, LanguageProfile

# Section headings, in order. This IS the reused taxonomy.
SECTION_ORDER: list[str] = [
    "IDENTITY",
    "LANGUAGE",
    "SPEAKING RULES",
    "FACTS",
    "STYLE / VOICE",
    "THE GOAL",
    "CONVERSATION FLOW",
    "QUALIFY",
    "BOOKING / SMS CONSENT",
    "CAPTURING DETAILS",
    "ESCALATION",
    "HARD RULES",
    "SAMPLE LINES",
]

# --- engine-owned constant bodies (never sourced from client YAML) ----------
_SPEAKING_RULES = """\
- You are on a phone call. Speak for the ear: short sentences, one idea at a time.
- Do not use markdown, bullet characters, emojis, or written formatting out loud.
- Say phone numbers, emails, and links naturally; offer to share them instead of reciting them.
- Confirm anything the caller gives you (name, number, spelling) by repeating it back.
- If the caller interrupts, stop talking and listen.
- Never talk over the caller or rush them."""

_STYLE_VOICE = """\
- Warm, concise, and confident — a helpful concierge, not a script reader.
- Mirror the caller's pace and energy; stay calm and steady if they are frustrated.
- Prefer plain language over jargon. Keep each turn short so the caller can respond.
- Ask one question at a time."""

_THE_GOAL = """\
In priority order:
1. Understand why the caller is reaching out.
2. Answer their question using only the FACTS section.
3. When appropriate, move them toward the next step (booking or a follow-up).
4. Capture the details the team needs (see CAPTURING DETAILS).
5. End the call politely."""

_CONVERSATION_FLOW = """\
- Open by asking how you can help, then listen for intent before offering solutions.
- Answer from FACTS. If it is not covered there, say so plainly and offer a message or escalation.
- Where it fits the caller's need, guide them toward booking.
- Before ending, confirm the key details you captured.
- Close warmly and briefly."""

_QUALIFY = """\
Capture what matters naturally, woven into the conversation — never interrogate:
- Why they are calling and how soon they need help.
- Whether they are a new or returning caller.
- The best name and number to reach them.
Ask only what is relevant, one question at a time."""

# Appended to HARD RULES when guardrails.preset == "medical_adjacent".
_MEDICAL_ADJACENT_RULES = [
    "Do not diagnose conditions, interpret symptoms, or give medical advice.",
    "Do not guarantee results or outcomes from any treatment.",
    "Do not provide dosage, medication, or aftercare medical instructions.",
    "Do not claim any treatment is FDA-approved, FDA-cleared, or a cure.",
    "For anything clinical or health-related, defer to licensed staff.",
]


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _identity(config: ClientConfig) -> str:
    meta = config.client
    return (
        f"You are the virtual phone receptionist for {meta.business_name}, "
        f"a {meta.vertical}. You answer inbound calls, help callers with what they "
        f"need, and capture the information the team uses to follow up. You are "
        f"professional, warm, and efficient, and you represent the business "
        f"accurately. If a caller asks whether you are a person, say plainly that "
        f"you are a virtual assistant for {meta.business_name}."
    )


def _language(config: ClientConfig, language: LanguageProfile) -> str:
    lines = [f"Your primary language on this line is {language.code}."]
    if language.language_notes and language.language_notes.strip():
        lines.append(language.language_notes.strip())
    others = [lp.code for lp in config.languages if lp.code != language.code]
    if others:
        joined = ", ".join(others)
        lines.append(
            f"If the caller speaks or switches to one of these languages "
            f"({joined}), continue naturally in that language without commenting "
            f"on the switch."
        )
    return "\n".join(lines)


def _facts(config: ClientConfig) -> str:
    facts = config.facts
    meta = config.client
    blocks: list[str] = [
        f"These are the only facts you may state about {meta.business_name}. If a "
        f"caller asks something not covered here, say you are not certain and offer "
        f"to take a message or connect them to the team. Never invent details, "
        f"prices, or availability.",
        f"About: {facts.description.strip()}",
    ]
    if facts.address:
        blocks.append(f"Address: {facts.address}")
    if facts.phone:
        blocks.append(f"Phone: {facts.phone}")
    if facts.email:
        blocks.append(f"Email: {facts.email}")
    if facts.hours:
        rows = [f"- {h.days}: {h.open} to {h.close}" for h in facts.hours]
        blocks.append("Hours:\n" + "\n".join(rows))
    if facts.services:
        rows = []
        for s in facts.services:
            line = f"- {s.name}"
            if s.description:
                line += f": {s.description}"
            if s.price:
                line += f" ({s.price})"
            rows.append(line)
        blocks.append("Services:\n" + "\n".join(rows))
    if facts.faq:
        rows = [f"- Q: {item.q}\n  A: {item.a}" for item in facts.faq]
        blocks.append("Frequently asked:\n" + "\n".join(rows))
    if config.booking.url:
        blocks.append(f"Booking link: {config.booking.url}")
    return "\n\n".join(blocks)


def _booking(config: ClientConfig) -> str:
    booking = config.booking
    if booking.url and booking.sms_consent:
        return (
            "When the caller is ready to book, offer to text them the booking "
            "link. Ask permission first — for example, 'Can I text you the booking "
            "link?' Only send it after they say yes, and confirm the number back "
            "to them before sending."
        )
    if booking.url and not booking.sms_consent:
        return (
            "When the caller is ready to book, point them to the booking link. Do "
            "not offer to send a text message — SMS is not enabled for this line. "
            "Share the link through the channel the caller prefers."
        )
    return (
        "Online booking is not available on this line. Offer to take the caller's "
        "details so the team can follow up to schedule, or hand off per the "
        "ESCALATION section."
    )


def _capturing_details(config: ClientConfig) -> str:
    intro = (
        "Before the call ends, make sure you have captured the following for the "
        "team's records. Gather them conversationally, never as a rigid form:"
    )
    caller_fields = [f for f in config.post_call if f.source == "caller"]
    rows = [f"- {field.name}: {field.description}" for field in caller_fields]
    outro = "Read back the caller's name, number, and email to confirm accuracy."
    lines = [intro, "\n".join(rows), outro]
    if any(f.source == "derived" for f in config.post_call):
        lines.append(
            "Additional summary fields are derived automatically from the call "
            "afterward — you do not need to ask about them."
        )
    return "\n".join(lines)


def _escalation(config: ClientConfig) -> str:
    esc = config.escalation
    lines = [f"Hand off to {esc.contact_name} when any of the following happens:"]
    if esc.escalate_when:
        lines.append(_bullets(esc.escalate_when))
    else:
        lines.append("- The caller asks for something beyond what you can answer.")
    if esc.handoff_message and esc.handoff_message.strip():
        lines.append(f"When you escalate, say something like: {esc.handoff_message.strip()}")
    lines.append(
        "Never guess at answers outside your knowledge — escalate to "
        f"{esc.contact_name} instead of bluffing."
    )
    return "\n".join(lines)


def _hard_rules(config: ClientConfig) -> str:
    meta = config.client
    rules = [
        "Never claim to be a human; you are a virtual assistant.",
        "Never invent facts, prices, hours, or availability that are not in FACTS.",
        "Never share information about other callers or internal operations.",
    ]
    rules.extend(config.guardrails.never_say)
    lines = [_bullets(rules)]
    if config.guardrails.off_limits:
        lines.append(
            "Do not discuss these off-limits topics; redirect politely:\n"
            + _bullets(config.guardrails.off_limits)
        )
    if config.guardrails.preset == "medical_adjacent":
        lines.append(
            f"Because {meta.business_name} is health-related, also follow these "
            "hard limits:\n" + _bullets(_MEDICAL_ADJACENT_RULES)
        )
    return "\n\n".join(lines)


def _sample_lines(config: ClientConfig) -> str:
    meta = config.client
    contact = config.escalation.contact_name
    samples = [
        f'Opening: "Thanks for calling {meta.business_name}. How can I help you today?"',
        'Not sure: "That is a great question — let me have someone follow up so you '
        'get the right answer."',
        'Booking: "I can help you get that scheduled."',
        f'Escalation: "Let me get {contact}, who can help you with that."',
        f'Closing: "Thanks for calling {meta.business_name}. Have a great day."',
    ]
    intro = "Adapt these naturally to the conversation — do not recite them word for word:"
    return intro + "\n" + _bullets(samples)


def compile_prompt(config: ClientConfig, language: LanguageProfile) -> str:
    """Compile a deterministic general_prompt for one language of one client."""
    bodies: dict[str, str] = {
        "IDENTITY": _identity(config),
        "LANGUAGE": _language(config, language),
        "SPEAKING RULES": _SPEAKING_RULES,
        "FACTS": _facts(config),
        "STYLE / VOICE": _STYLE_VOICE,
        "THE GOAL": _THE_GOAL,
        "CONVERSATION FLOW": _CONVERSATION_FLOW,
        "QUALIFY": _QUALIFY,
        "BOOKING / SMS CONSENT": _booking(config),
        "CAPTURING DETAILS": _capturing_details(config),
        "ESCALATION": _escalation(config),
        "HARD RULES": _hard_rules(config),
        "SAMPLE LINES": _sample_lines(config),
    }
    blocks = [f"# {name}\n{bodies[name].rstrip()}" for name in SECTION_ORDER]
    return "\n\n".join(blocks) + "\n"


def compile_all(config: ClientConfig) -> dict[str, str]:
    """Compile prompts for every language, keyed by language code."""
    return {lp.code: compile_prompt(config, lp) for lp in config.languages}
