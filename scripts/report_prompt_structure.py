"""Analyze the STRUCTURE of captured Retell general_prompt / begin_message values.

Reads the per-agent JSON dumps under capture/retell/ and writes
capture/retell/_prompt_structure.md — a structural analysis that lists section
headings, compares section presence/similarity across agents, and counts (never
quotes) embedded client facts.

Privacy contract: this report emits ONLY heading text, character counts,
similarity percentages, and pattern counts. No prompt body / sentence text is
ever written. Both the report and this analysis live under capture/, which is
gitignored.
"""

from __future__ import annotations

import glob
import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURE_DIR = REPO_ROOT / "capture" / "retell"
OUT_PATH = CAPTURE_DIR / "_prompt_structure.md"

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

# --- variable-content detectors (pattern-based counts only, never captured) ---
PATTERNS = {
    "phone": re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}"),
    "email": re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+"),
    "url": re.compile(r"https?://\S+|\bwww\.\S+|\b[\w\-]+\.(?:com|net|org|io|ai|co)\b"),
    "address": re.compile(
        r"\b\d{1,6}\s+(?:[A-Z][a-zA-Z]+\.?\s+){1,4}"
        r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|"
        r"Suite|Ste|Unit|Way|Court|Ct|Place|Pl|Highway|Hwy)\b"
    ),
    "price": re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?|\b\d+\s?(?:dollars|USD)\b", re.IGNORECASE),
    "time_hours": re.compile(
        r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b"
        r"|\b(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)\b"
        r"|\b\d{1,2}\s?[-–]\s?\d{1,2}\b",
        re.IGNORECASE,
    ),
    # Approximate: 2+ consecutive Title-Case words (proper-noun-ish phrases).
    "propernoun": re.compile(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+\b"),
}

SPANISH_CHARS = set("áéíóúñ¿¡üÁÉÍÓÚÑÜ")


def detect_langs(text: str) -> str:
    langs = set()
    if any(c in SPANISH_CHARS for c in text):
        langs.add("es")
    if re.search(r"\b(the|you|your|and|please|call|for|with)\b", text.lower()):
        langs.add("en")
    return ",".join(sorted(langs)) or "undetermined"


def normalize_heading(text: str) -> str:
    """Normalize a heading for cross-agent matching: drop parentheticals/qualifiers."""
    t = text
    for cut in ("(", "—", "–", " - ", "/"):
        idx = t.find(cut)
        if idx > 0:
            t = t[:idx]
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def parse_sections(text: str) -> list[dict]:
    """Split a prompt into sections at markdown headings; body kept locally only."""
    sections: list[dict] = []
    current = {"level": 0, "heading": "(preamble)", "norm": "(preamble)", "body_lines": []}
    for line in text.split("\n"):
        m = HEADING_RE.match(line.strip())
        if m:
            if current["body_lines"] or current["heading"] != "(preamble)":
                sections.append(current)
            current = {
                "level": len(m.group(1)),
                "heading": m.group(2).strip(),
                "norm": normalize_heading(m.group(2)),
                "body_lines": [],
            }
        else:
            current["body_lines"].append(line)
    sections.append(current)

    for s in sections:
        body = "\n".join(s["body_lines"]).strip()
        s["body"] = body
        s["body_chars"] = len(body)
    # Drop an empty preamble.
    return [s for s in sections if not (s["heading"] == "(preamble)" and s["body_chars"] == 0)]


def count_patterns(text: str) -> dict[str, int]:
    return {name: len(rx.findall(text)) for name, rx in PATTERNS.items()}


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def classify_similarity(a: str, b: str) -> str:
    if a == b:
        return "VERBATIM-IDENTICAL"
    r = similarity(a, b)
    if r > 0.90:
        return "NEAR-IDENTICAL"
    return "DIFFERENT"


def _business_token(agent_name: str) -> str:
    """The leading alphanumeric token of an agent_name, used as its short label."""
    m = re.match(r"\s*([A-Za-z0-9]+)", agent_name)
    return m.group(1) if m else agent_name


def _agent_language(agent: dict, begin_message: str) -> str:
    """'ES' if the agent is Spanish, else 'EN' — derived from the captured data."""
    name = agent.get("agent_name", "") or ""
    if re.search(r"espa", name, re.IGNORECASE):
        return "ES"
    langs = detect_langs(begin_message).split(",")
    return "ES" if ("es" in langs and "en" not in langs) else "EN"


def load_agents() -> list[dict]:
    """Load captured agents; labels are DERIVED from agent_name in the JSON at
    runtime (business token + language), never hardcoded — so no client/agent
    name lives in this source file. A language suffix is added only when a
    business has more than one agent (to disambiguate a bilingual pair)."""
    agents = []
    for path in sorted(glob.glob(str(CAPTURE_DIR / "agent_*.json"))):
        data = json.load(open(path, encoding="utf-8"))
        agent = data.get("agent") or {}
        detail = data.get("response_engine_detail") or {}
        name = agent.get("agent_name") or Path(path).stem
        begin = detail.get("begin_message", "") or ""
        prompt = detail.get("general_prompt", "") or ""
        agents.append(
            {
                "name": name,
                "business": _business_token(name),
                "lang": _agent_language(agent, begin),
                "prompt": prompt,
                "begin_message": begin,
                "sections": parse_sections(prompt),
            }
        )
    counts: dict[str, int] = {}
    for a in agents:
        counts[a["business"]] = counts.get(a["business"], 0) + 1
    for a in agents:
        a["label"] = f"{a['business']}-{a['lang']}" if counts[a["business"]] > 1 else a["business"]
    lang_order = {"EN": 0, "ES": 1}
    agents.sort(key=lambda a: (a["business"], lang_order.get(a["lang"], 9)))
    return agents


def build_report(agents: list[dict]) -> str:
    lines: list[str] = []
    w = lines.append
    labels = [a["label"] for a in agents]

    # Derive (never hardcode) the bilingual "family" business and the single-agent
    # businesses from the loaded data, for the report narrative.
    by_business: dict[str, list[dict]] = {}
    for a in agents:
        by_business.setdefault(a["business"], []).append(a)
    family = next((b for b, ags in by_business.items() if len(ags) > 1), None)
    singles = [a["business"] for a in agents if len(by_business[a["business"]]) == 1]
    single = singles[0] if singles else "(none)"

    w("# Prompt structure analysis")
    w("")
    w(
        "Structural analysis of the three captured `general_prompt` values (plus "
        "`begin_message`). **No prompt body text appears here** — only heading "
        "text, character counts, similarity scores, and pattern counts."
    )
    w("")
    for a in agents:
        w(f"- **{a['label']}** — {a['name']} — prompt {len(a['prompt'])} chars, "
          f"{len(a['sections'])} sections")
    w("")

    # ---------- (a) Per-prompt section outline ----------
    w("## (a) Section outlines")
    w("")
    for a in agents:
        w(f"### {a['label']} — {len(a['sections'])} sections")
        w("")
        w("| # | Lvl | Heading | Body chars |")
        w("|---|---|---|---|")
        for i, s in enumerate(a["sections"], 1):
            heading = s["heading"].replace("|", "\\|")
            w(f"| {i} | {'#' * s['level'] or '—'} | {heading} | {s['body_chars']} |")
        w("")

    # ---------- (b) Cross-agent section presence + body comparison ----------
    w("## (b) Cross-agent section comparison")
    w("")
    # Map normalized heading -> {label: section}
    norm_map: dict[str, dict[str, dict]] = {}
    for a in agents:
        for s in a["sections"]:
            norm_map.setdefault(s["norm"], {})[a["label"]] = s

    in_all = [n for n, m in norm_map.items() if len(m) == len(agents)]
    in_some = [n for n, m in norm_map.items() if 1 < len(m) < len(agents)]
    in_one = [n for n, m in norm_map.items() if len(m) == 1]

    w(f"- Distinct normalized section names: **{len(norm_map)}**")
    w(f"- In **all {len(agents)}** prompts: **{len(in_all)}**")
    w(f"- In **some** (2 of 3): **{len(in_some)}**")
    w(f"- In **one** only: **{len(in_one)}**")
    w("")
    w(f"Heading conventions differ by family: the {family} prompts use `#` ALL-CAPS "
      f"headings (shared taxonomy); {single} uses `##` Title-Case headings (its "
      f"own taxonomy). Section-name matching therefore aligns {family}-EN/ES to each "
      f"other, not to {single}.")
    w("")

    w("### Presence matrix")
    w("")
    w("| Normalized section | " + " | ".join(labels) + " | Body similarity |")
    w("|---" * (len(labels) + 2) + "|")
    # Order: all-three first, then some, then one; stable by first appearance.
    ordered = in_all + in_some + in_one
    for norm in ordered:
        m = norm_map[norm]
        cells = ["✓" if lab in m else "—" for lab in labels]
        present_labels = [lab for lab in labels if lab in m]
        if len(present_labels) > 1:
            bodies = [m[lab]["body"] for lab in present_labels]
            if all(b == bodies[0] for b in bodies):
                sim = "VERBATIM-IDENTICAL"
            else:
                pairwise_min = min(
                    similarity(bodies[i], bodies[j])
                    for i in range(len(bodies))
                    for j in range(i + 1, len(bodies))
                )
                if pairwise_min > 0.90:
                    sim = f"NEAR-IDENTICAL (min {pairwise_min:.0%})"
                else:
                    sim = f"DIFFERENT (min {pairwise_min:.0%})"
        else:
            sim = "— (unique)"
        disp = norm if norm else "(blank)"
        w(f"| {disp} | " + " | ".join(cells) + f" | {sim} |")
    w("")

    # ---------- (c) Variable content counts ----------
    w("## (c) Variable client-fact counts (pattern-detected, never quoted)")
    w("")
    w("Counts of embedded facts per section per agent. Categories: phone, email, "
      "url, address, price, time_hours, propernoun (proper-noun business names "
      "— approximate, via multi-word Title-Case detection).")
    w("")
    for a in agents:
        w(f"### {a['label']}")
        w("")
        w("| Section | phone | email | url | address | price | time_hours | propernoun |")
        w("|---|---|---|---|---|---|---|---|")
        totals = dict.fromkeys(PATTERNS, 0)
        for s in a["sections"]:
            c = count_patterns(s["body"])
            for k in totals:
                totals[k] += c[k]
            heading = s["heading"].replace("|", "\\|")
            w(f"| {heading} | {c['phone']} | {c['email']} | {c['url']} | "
              f"{c['address']} | {c['price']} | {c['time_hours']} | {c['propernoun']} |")
        w(f"| **TOTAL** | {totals['phone']} | {totals['email']} | {totals['url']} | "
          f"{totals['address']} | {totals['price']} | {totals['time_hours']} | "
          f"{totals['propernoun']} |")
        w("")

    # ---------- (d) bilingual family EN vs ES ----------
    w(f"## (d) {family} English vs Español")
    w("")
    en = next((a for a in agents if a["business"] == family and a["lang"] == "EN"), None)
    es = next((a for a in agents if a["business"] == family and a["lang"] == "ES"), None)
    if en and es:
        overall = similarity(en["prompt"], es["prompt"])
        w(f"- Overall prompt similarity (char-level): **{overall:.1%}**")
        hdr_en = [s["heading"] for s in en["sections"]]
        hdr_es = [s["heading"] for s in es["sections"]]
        w(f"- Heading sets identical: **{hdr_en == hdr_es}** "
          f"(EN {len(hdr_en)} headings, ES {len(hdr_es)} headings)")
        w("")
        w("Per shared section: EN vs ES body similarity, ES length delta vs EN, "
          "and a flag when ES body length differs from EN by more than 30% "
          "(candidate for actual content divergence rather than plain translation).")
        w("")
        w("| Section | EN chars | ES chars | ES Δlen | >30%? | Body similarity |")
        w("|---|---|---|---|---|---|")
        es_by_norm = {s["norm"]: s for s in es["sections"]}
        for s in en["sections"]:
            e = es_by_norm.get(s["norm"])
            if not e:
                w(f"| {s['heading']} | {s['body_chars']} | _absent_ | — | — | EN-only |")
                continue
            en_len = s["body_chars"] or 1
            delta = (e["body_chars"] - s["body_chars"]) / en_len
            flag = "⚠ yes" if abs(delta) > 0.30 else ""
            cls = classify_similarity(s["body"], e["body"])
            heading = s["heading"].replace("|", "\\|")
            w(f"| {heading} | {s['body_chars']} | {e['body_chars']} | "
              f"{delta:+.0%} | {flag} | {cls} |")
        w("")
    else:
        w(f"_{family} EN/ES pair not both present._")
        w("")

    # ---------- (e) Engine-template candidates ----------
    w("## (e) Engine-template candidates (verbatim across ALL THREE)")
    w("")
    template_sections = []
    for norm in in_all:
        m = norm_map[norm]
        bodies = [m[lab]["body"] for lab in labels if lab in m]
        if bodies and all(b == bodies[0] for b in bodies):
            template_sections.append(norm)
    if template_sections:
        w("Sections whose body is byte-identical across all three prompts:")
        for n in template_sections:
            w(f"- {n}")
    else:
        w("_No section is verbatim-identical across all three prompts._ "
          f"(Expected: the {family} family and {single} use different taxonomies and "
          f"wording, and {family}-ES bodies are translated.)")
    w("")
    # Bonus: identical body blocks across all three regardless of heading name.
    def body_hashes(a: dict) -> set[str]:
        return {
            hashlib.sha256(s["body"].encode("utf-8")).hexdigest()
            for s in a["sections"]
            if s["body_chars"] > 0
        }
    common_bodies = set.intersection(*[body_hashes(a) for a in agents]) if agents else set()
    w(f"Cross-check — identical body blocks shared by all three regardless of "
      f"heading name: **{len(common_bodies)}**.")
    w("")

    # ---------- begin_message ----------
    w("## begin_message (opening line)")
    w("")
    w("| Agent | chars | language | phone | email | url | price | time_hours |")
    w("|---|---|---|---|---|---|---|---|")
    for a in agents:
        bm = a["begin_message"]
        c = count_patterns(bm)
        w(f"| {a['label']} | {len(bm)} | {detect_langs(bm)} | {c['phone']} | "
          f"{c['email']} | {c['url']} | {c['price']} | {c['time_hours']} |")
    w("")
    if en and es:
        w(f"- {family} EN vs ES begin_message similarity: "
          f"**{similarity(en['begin_message'], es['begin_message']):.1%}** "
          f"({classify_similarity(en['begin_message'], es['begin_message'])}).")
        w("")

    return "\n".join(lines) + "\n"


def main() -> None:
    agents = load_agents()
    if not agents:
        raise SystemExit(f"No agent_*.json files found under {CAPTURE_DIR}")
    report = build_report(agents)
    OUT_PATH.write_text(report, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(report)} chars) covering {len(agents)} agents.")


if __name__ == "__main__":
    main()
