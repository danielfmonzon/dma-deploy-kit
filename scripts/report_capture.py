"""Generate a sanitized field-inventory report from captured Retell agent configs.

Reads the per-agent JSON dumps under capture/retell/ (produced by
capture_retell.py) and writes capture/retell/_field_report.md — an internal
analysis aid that inventories every field path, flags differences, and calls out
external IDs / URLs / phone numbers.

By construction this report NEVER emits prompt text. Any field whose leaf name is
prompt-like (general_prompt, begin_message, tool/analysis descriptions, state or
node text, etc.) is reported only as a character length plus a rough detected
language — never its contents. The report file lands in capture/, which is
gitignored.
"""

from __future__ import annotations

import glob
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURE_DIR = REPO_ROOT / "capture" / "retell"
OUT_PATH = CAPTURE_DIR / "_field_report.md"

# Leaf names whose string values are free-form / prompt-like: never print them.
PROMPT_LEAVES = {
    "general_prompt",
    "begin_message",
    "prompt",
    "instruction",
    "instructions",
    "description",
    "message",
    "text",
    "content",
    "examples",
    "example",
}
# Path fragments that indicate prompt-like text regardless of leaf name.
PROMPT_PATH_FRAGMENTS = (".states", ".nodes")

SPANISH_CHARS = set("áéíóúñ¿¡üÁÉÍÓÚÑÜ")
SPANISH_WORDS = {
    "hola", "gracias", "por", "favor", "usted", "buenos", "buenas", "dias",
    "días", "como", "cómo", "esta", "está", "español", "llamada", "cita",
    "para", "con", "que", "qué", "sí", "recepcion", "recepción", "hoy",
    "cliente", "puede", "ayudar", "nombre", "hora",
}
ENGLISH_WORDS = {
    "the", "you", "and", "your", "please", "hello", "call", "appointment",
    "for", "with", "are", "this", "receptionist", "how", "can", "help",
    "name", "today", "thanks", "thank", "time", "our",
}


def detect_languages(text: str) -> list[str]:
    """Very rough language heuristic — good enough to label prompt fields."""
    langs: set[str] = set()
    if any(ch in SPANISH_CHARS for ch in text):
        langs.add("es")
    words = set(re.findall(r"[a-záéíóúñü]+", text.lower()))
    if words & SPANISH_WORDS:
        langs.add("es")
    if words & ENGLISH_WORDS:
        langs.add("en")
    if not langs:
        langs.add("undetermined")
    return sorted(langs)


def leaf_name(path: str) -> str:
    return path.split(".")[-1].split("[")[0]


def is_prompt_like(path: str, value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if leaf_name(path) in PROMPT_LEAVES:
        return True
    return any(frag in path for frag in PROMPT_PATH_FRAGMENTS)


def json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def flatten(obj: object, prefix: str = "") -> dict[str, object]:
    """Flatten nested dicts/lists into dotted paths. Lists of objects use [i]."""
    out: dict[str, object] = {}
    if isinstance(obj, dict):
        if not obj:
            out[prefix] = {}
        for key, val in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            out.update(flatten(val, path))
    elif isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            for i, item in enumerate(obj):
                out.update(flatten(item, f"{prefix}[{i}]"))
        else:
            out[prefix] = obj  # scalar list or empty list -> leaf
    else:
        out[prefix] = obj
    return out


def classify_sensitive(path: str, value: object) -> str | None:
    """Return a label if the value looks like a secret/URL/phone/external ID."""
    leaf = leaf_name(path).lower()
    if "timestamp" in leaf:
        return None
    if isinstance(value, str):
        if re.search(r"https?://", value):
            return "URL"
        # Phone: mostly digits/punctuation, at least 7 digits.
        if re.fullmatch(r"\+?[\d][\d\s().\-]{6,}", value) and sum(c.isdigit() for c in value) >= 7:
            return "phone number"
    if any(t in leaf for t in ("secret", "token", "password", "api_key", "apikey")):
        return "secret"
    if leaf == "webhook_url" or leaf.endswith("_url"):
        return "URL"
    if "voice_id" in leaf:
        return "voice ID"
    if "knowledge_base" in leaf:
        return "knowledge base ID"
    if leaf == "llm_id":
        return "external ID (Retell LLM)"
    if leaf == "conversation_flow_id":
        return "external ID (conversation flow)"
    if leaf == "agent_id":
        return "external ID (agent)"
    if leaf.endswith("_id") or leaf.endswith("_ids"):
        return "external ID"
    return None


def fmt_value(value: object) -> str:
    """Compact one-line rendering of a non-prompt value for a markdown cell."""
    s = json.dumps(value, ensure_ascii=False)
    s = s.replace("|", "\\|").replace("\n", " ")
    if len(s) > 120:
        s = s[:117] + "..."
    return s


def load_agents() -> list[dict]:
    agents = []
    for path in sorted(glob.glob(str(CAPTURE_DIR / "agent_*.json"))):
        data = json.load(open(path, encoding="utf-8"))
        agent = data.get("agent") or {}
        agents.append(
            {
                "file": Path(path).name,
                "agent_id": agent.get("agent_id", Path(path).stem),
                "name": agent.get("agent_name") or "(unnamed)",
                "raw": data,
                "flat": flatten(data),
            }
        )
    # Stable ordering by name for reproducible columns.
    agents.sort(key=lambda a: a["name"])
    return agents


def build_report(agents: list[dict]) -> str:
    labels = [a["name"] for a in agents]
    lines: list[str] = []
    w = lines.append

    w("# Retell capture — field inventory report")
    w("")
    w(
        "Sanitized internal analysis of the captured Retell agent configs. "
        "**No prompt text appears in this report** — prompt-like fields are "
        "reported only as character length and a rough detected language."
    )
    w("")
    w(f"- Agents analyzed: **{len(agents)}**")
    for a in agents:
        w(f"  - {a['name']} — `{a['agent_id']}` (from `{a['file']}`)")
    w("")

    # ---- Union of all field paths ----
    all_paths: set[str] = set()
    for a in agents:
        all_paths.update(a["flat"].keys())

    def present(path: str) -> list[dict]:
        return [a for a in agents if path in a["flat"]]

    def status(path: str) -> str:
        holders = present(path)
        if len(holders) < len(agents):
            return "PRESENT IN SOME"
        serialized = {
            json.dumps(a["flat"][path], sort_keys=True, ensure_ascii=False) for a in holders
        }
        return "IDENTICAL" if len(serialized) == 1 else "DIFFERS"

    def type_of(path: str) -> str:
        types = sorted({json_type(a["flat"][path]) for a in present(path)})
        return types[0] if len(types) == 1 else "mixed: " + "/".join(types)

    # =========================================================
    # (d) Response engine summary
    # =========================================================
    w("## Response engine summary")
    w("")
    w("| Agent | Engine type | Model | Structure | Tools |")
    w("|---|---|---|---|---|")
    for a in agents:
        re_obj = a["raw"].get("agent", {}).get("response_engine") or {}
        engine_type = re_obj.get("type", "(unknown)")
        detail = a["raw"].get("response_engine_detail") or {}
        model = detail.get("model", "(n/a)")
        has_states = "states" in detail or "nodes" in detail
        structure = "states/nodes graph" if has_states else "single flat prompt (general_prompt)"
        tools = detail.get("general_tools") or []
        tool_names = [t.get("name", "(unnamed)") for t in tools if isinstance(t, dict)]
        tool_str = ", ".join(f"`{n}`" for n in tool_names) if tool_names else "(none)"
        w(f"| {a['name']} | `{engine_type}` | `{model}` | {structure} | {tool_str} |")
    w("")

    # =========================================================
    # (a) Full field inventory
    # =========================================================
    w("## Field inventory")
    w("")
    w(f"Every field path found across the {len(agents)} agents (agent + "
      "response_engine_detail objects), flattened to dotted paths.")
    w("")
    w("| Field path | Type | Status | Prompt-like |")
    w("|---|---|---|---|")
    for path in sorted(all_paths):
        is_prompt = any(is_prompt_like(path, a["flat"][path]) for a in present(path))
        prompt_flag = "yes" if is_prompt else ""
        w(f"| `{path}` | {type_of(path)} | {status(path)} | {prompt_flag} |")
    w("")

    # =========================================================
    # (b) Differences (non-prompt) shown side by side
    # =========================================================
    w("## Differences (non-prompt fields)")
    w("")
    w("Fields that DIFFER or are PRESENT IN SOME agents, with values side by "
      "side. Absent = the agent has no such field. Prompt-like fields are "
      "excluded here and summarized separately below.")
    w("")
    header = "| Field path | " + " | ".join(labels) + " |"
    w(header)
    w("|---" * (len(labels) + 1) + "|")
    diff_rows = 0
    for path in sorted(all_paths):
        st = status(path)
        if st == "IDENTICAL":
            continue
        if any(is_prompt_like(path, a["flat"][path]) for a in present(path)):
            continue
        cells = []
        for a in agents:
            if path in a["flat"]:
                cells.append(fmt_value(a["flat"][path]))
            else:
                cells.append("_absent_")
        w(f"| `{path}` | " + " | ".join(cells) + " |")
        diff_rows += 1
    if diff_rows == 0:
        w("| _(no non-prompt differences)_ | " + " | ".join([""] * len(labels)) + " |")
    w("")

    # =========================================================
    # (b cont.) Prompt-like fields: length + language only
    # =========================================================
    w("## Prompt-like fields (length + language only)")
    w("")
    w("Free-form / prompt text is never shown. Each cell is `<chars> chars "
      "[langs]`, or _absent_.")
    w("")
    prompt_paths = sorted(
        p for p in all_paths if any(is_prompt_like(p, a["flat"][p]) for a in present(p))
    )
    if prompt_paths:
        w("| Field path | " + " | ".join(labels) + " |")
        w("|---" * (len(labels) + 1) + "|")
        for path in prompt_paths:
            cells = []
            for a in agents:
                if path in a["flat"] and isinstance(a["flat"][path], str):
                    val = a["flat"][path]
                    langs = ",".join(detect_languages(val))
                    cells.append(f"{len(val)} chars [{langs}]")
                elif path in a["flat"]:
                    cells.append("(non-string)")
                else:
                    cells.append("_absent_")
            w(f"| `{path}` | " + " | ".join(cells) + " |")
    else:
        w("_No prompt-like fields detected._")
    w("")

    # =========================================================
    # (c) Secrets / URLs / phone numbers / external IDs
    # =========================================================
    w("## ⚠ Secrets, URLs, phone numbers & external IDs")
    w("")
    w("Values shown for classification. **Treat as sensitive** — these should "
      "be parameterized per client, never hard-coded.")
    w("")
    sensitive_rows = []
    for path in sorted(all_paths):
        for a in agents:
            if path not in a["flat"]:
                continue
            label = classify_sensitive(path, a["flat"][path])
            if label:
                sensitive_rows.append((path, a["name"], label, fmt_value(a["flat"][path])))
    if sensitive_rows:
        w("| Field path | Agent | Flag | Value |")
        w("|---|---|---|---|")
        for path, name, label, val in sensitive_rows:
            w(f"| `{path}` | {name} | **{label}** | `{val}` |")
    else:
        w("_No secret/URL/phone/external-ID fields detected._")
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
