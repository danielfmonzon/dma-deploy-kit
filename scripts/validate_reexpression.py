"""Validate the DMA re-expression against the captured production prompts.

Loads config/clients/dma.yaml, compiles both language prompts, and compares them
section-by-section against the captured production general_prompts under
capture/retell/. Writes capture/retell/_reexpression_report.md.

Privacy: the report contains ONLY character counts, similarity percentages, and
neutral one-line judgments — never production prompt text. Production text is
read at runtime from the gitignored capture/ dump; it never enters this committed
script or the report. A leak-check at the end verifies this.
"""

from __future__ import annotations

import glob
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from dma_deploy_kit.agent import compile_all
from dma_deploy_kit.agent.prompt import SECTION_ORDER
from dma_deploy_kit.config import load_client_config

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_DIR = REPO_ROOT / "capture" / "retell"
DMA_CONFIG = REPO_ROOT / "config" / "clients" / "dma.yaml"
OUT_PATH = CAPTURE_DIR / "_reexpression_report.md"

HEAD = re.compile(r"^# (.+)$", re.MULTILINE)

# Authored, neutral coverage judgments per production section (no production text).
COVERAGE = {
    "IDENTITY": (
        "partial",
        "Persona regenerated from business_name + vertical; production framing not carried.",
    ),
    "LANGUAGE": (
        "full",
        "Per-language directive carried verbatim via each profile's language_notes.",
    ),
    "SPEAKING RULES": (
        "partial",
        "Engine-owned generic phone rules cover the concept; not client-configurable.",
    ),
    "FACTS": (
        "partial",
        "One description is represented; framing bullets have no home, no concrete facts existed.",
    ),
    "STYLE / VOICE": (
        "partial",
        "Engine-owned generic voice guidance; production tone is not client-configurable.",
    ),
    "THE GOAL": (
        "partial",
        "Engine-owned generic goal ordering covers the concept; wording not configurable.",
    ),
    "CONVERSATION FLOW": (
        "partial",
        "Engine-owned generic flow covers the concept; specific steps not configurable.",
    ),
    "QUALIFY": (
        "partial",
        "Engine-owned generic qualifying guidance; overlaps caller-sourced post_call fields.",
    ),
    "BOOKING / SMS CONSENT": (
        "partial",
        "Only a URL and sms_consent flag are representable; booking script not carried.",
    ),
    "CAPTURING DETAILS": (
        "full",
        "Driven directly by the caller-sourced post_call fields.",
    ),
    "ESCALATION": (
        "full",
        "Contact name and escalation triggers carried via config.",
    ),
    "HARD RULES": (
        "full",
        "Client prohibitions carried via never_say plus the engine medical_adjacent preset.",
    ),
    "SAMPLE LINES": (
        "missing",
        "Schema has no field for client-authored sample lines; engine emits generic ones.",
    ),
}

# Authored, neutral list of production prompt content the schema cannot represent.
SCHEMA_GAPS = [
    "Client-authored SAMPLE LINES: no config field exists for bespoke example phone lines.",
    "FACTS framing/rules bullets: only a single free-text description is representable, "
    "not a structured list of guidance.",
    "Client-tuned SPEAKING RULES / STYLE / THE GOAL / CONVERSATION FLOW / QUALIFY prose: "
    "these sections are engine-owned and not client-configurable.",
    "Multi-step BOOKING / SMS CONSENT script: schema captures only a URL and a boolean "
    "consent flag, not the conversational booking flow.",
    "IDENTITY persona nuance: identity is regenerated from business_name + vertical rather "
    "than carried verbatim.",
]


def normalize_heading(text: str) -> str:
    t = text
    for cut in ("(", "—", "–"):
        idx = t.find(cut)
        if idx > 0:
            t = t[:idx]
    return t.strip().upper()


def parse_sections(prompt: str) -> dict[str, str]:
    parts = re.split(r"^# (.+)$", prompt, flags=re.MULTILINE)
    out: dict[str, str] = {}
    for i in range(1, len(parts), 2):
        out[normalize_heading(parts[i])] = parts[i + 1].strip()
    return out


def ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def prod_prompt(agent_json: dict) -> str:
    return (agent_json.get("response_engine_detail") or {}).get("general_prompt", "")


def load_production() -> dict[str, dict]:
    prod = {}
    for f in sorted(glob.glob(str(CAPTURE_DIR / "agent_*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        name = (d.get("agent") or {}).get("agent_name", "")
        if "English" in name:
            prod["en-US"] = d
        elif "Espa" in name:
            prod["es-419"] = d
    return prod


def leak_check(report: str, prod: dict) -> int:
    """Return count of production body-text windows found in the report (want 0)."""
    report_norm = re.sub(r"\s+", " ", report)
    blocks = []
    for d in prod.values():
        det = d.get("response_engine_detail") or {}
        gp = det.get("general_prompt", "") or ""
        body_lines = [ln for ln in gp.split("\n") if not ln.strip().startswith("# ")]
        blocks.append("\n".join(body_lines))
        blocks.append(det.get("begin_message", "") or "")
    leaks = 0
    for block in blocks:
        norm = re.sub(r"\s+", " ", block).strip()
        for i in range(0, max(0, len(norm) - 30 + 1), 8):
            if norm[i:i + 30] in report_norm:
                leaks += 1
    return leaks


def build_report(config, compiled: dict[str, str], prod: dict) -> str:
    lines: list[str] = []
    w = lines.append

    w("# DMA re-expression validation")
    w("")
    w("Validates the private re-expression `config/clients/dma.yaml` (built from "
      "the captured production DMA configs) by compiling its prompts and comparing "
      "them to the captured production `general_prompt`s. **This report contains no "
      "production prompt text** — only character counts, similarity percentages, and "
      "neutral one-line judgments.")
    w("")

    # ---- overall totals ----
    w("## Overall totals")
    w("")
    w("| Language | Compiled chars | Production chars | Overall similarity |")
    w("|---|---|---|---|")
    for code in ("en-US", "es-419"):
        comp = compiled[code]
        prod_gp = prod_prompt(prod[code])
        w(f"| {code} | {len(comp)} | {len(prod_gp)} | {ratio(prod_gp, comp):.1%} |")
    w("")

    # ---- section-by-section (en-US) ----
    w("## Section-by-section — en-US")
    w("")
    w("Compiled vs production body char counts and difflib similarity per section "
      "(matched by the shared 13-section taxonomy).")
    w("")
    w("| Section | Compiled chars | Production chars | Similarity |")
    w("|---|---|---|---|")
    comp_secs = parse_sections(compiled["en-US"])
    prod_secs = parse_sections(prod_prompt(prod["en-US"]))
    for name in SECTION_ORDER:
        c = comp_secs.get(name, "")
        p = prod_secs.get(name, "")
        w(f"| {name} | {len(c)} | {len(p)} | {ratio(p, c):.1%} |")
    w("")

    # ---- es-419 note ----
    w("## Section-by-section — es-419")
    w("")
    comp_es = parse_sections(compiled["es-419"])
    prod_es = parse_sections(prod_prompt(prod["es-419"]))
    lang_sim = ratio(prod_es.get("LANGUAGE", ""), comp_es.get("LANGUAGE", ""))
    w("Both the production and compiled es-419 prompts differ from their en-US "
      "counterparts only in the LANGUAGE section; all other sections are identical "
      "to the en-US rows above.")
    w("")
    w("| Section | Compiled chars | Production chars | Similarity |")
    w("|---|---|---|---|")
    w(f"| LANGUAGE | {len(comp_es.get('LANGUAGE', ''))} | {len(prod_es.get('LANGUAGE', ''))} "
      f"| {lang_sim:.1%} |")
    w("")

    # ---- coverage ----
    w("## Coverage by concept")
    w("")
    w("For each production section, whether its content has a home in the compiled "
      "output. Judged by concept; neutral descriptions, no quotations.")
    w("")
    w("| Section | Coverage | Notes |")
    w("|---|---|---|")
    for name in SECTION_ORDER:
        verdict, note = COVERAGE[name]
        w(f"| {name} | {verdict.upper()} | {note} |")
    full = sum(1 for n in SECTION_ORDER if COVERAGE[n][0] == "full")
    partial = sum(1 for n in SECTION_ORDER if COVERAGE[n][0] == "partial")
    missing = sum(1 for n in SECTION_ORDER if COVERAGE[n][0] == "missing")
    w("")
    w(f"Coverage tally: **{full} full**, **{partial} partial**, **{missing} missing** "
      f"of {len(SECTION_ORDER)} sections.")
    w("")

    # ---- schema gaps ----
    w("## Schema gaps (production prompt content not representable)")
    w("")
    for gap in SCHEMA_GAPS:
        w(f"- {gap}")
    w("")

    return "\n".join(lines) + "\n"


def main() -> None:
    config = load_client_config(DMA_CONFIG)
    compiled = compile_all(config)
    prod = load_production()
    if set(prod) < {"en-US", "es-419"}:
        raise SystemExit("Could not find both production DMA agents under capture/retell/")

    report = build_report(config, compiled, prod)
    leaks = leak_check(report, prod)
    report += (
        f"\n## Leak-check\n\nProduction body-text windows (30 chars) found in this "
        f"report: **{leaks}**. A value of 0 confirms no production prompt sentences "
        f"leaked into the report.\n"
    )

    OUT_PATH.write_text(report, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(report)} chars).")
    print(f"Leak-check: {'PASS' if leaks == 0 else 'FAIL'} ({leaks} leaks)")


if __name__ == "__main__":
    main()
