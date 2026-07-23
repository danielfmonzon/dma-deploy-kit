"""Layer 3 — latency budget checks (advisory; no LLM, no network).

Compares Retell per-call latency percentiles against a small budget. The budget
values are **initial operating targets grounded in voice-UX responsiveness norms
and consistent with observed production behavior** — engine defaults expected to
be tuned as we gather more data, NOT a vendor SLA or contractual threshold.

Each check reads ``call["latency"]``: a dict of categories (e2e, llm, tts, asr),
each a dict containing at least ``p50``/``p90``/``num`` (``num`` = sample count).
Missing data is surfaced as findings rather than skipped — visibility over
comfort — which is acceptable because the layer is advisory by default.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LatencyBudget:
    e2e_p50: int = 3000
    e2e_p90: int = 4000
    llm_p90: int = 2500
    tts_p90: int = 400
    asr_p90: int = 1000


DEFAULT_BUDGET = LatencyBudget()


@dataclass(frozen=True)
class LatencyFinding:
    check: str
    message: str
    measured: float | None = None
    budget: int | None = None


# (category, percentile field, LatencyBudget attribute) for every budgeted metric.
# Order defines both evaluation order and the per-category grouping below.
_BUDGETED_METRICS = [
    ("e2e", "p50", "e2e_p50"),
    ("e2e", "p90", "e2e_p90"),
    ("llm", "p90", "llm_p90"),
    ("tts", "p90", "tts_p90"),
    ("asr", "p90", "asr_p90"),
]


def check_call_latency(call: dict, budget: LatencyBudget = DEFAULT_BUDGET) -> list[LatencyFinding]:
    """Compare one call's latency percentiles against ``budget``.

    Returns a list of LatencyFinding. Over-budget percentiles produce
    ``<attr>_over_budget`` findings; absent data produces ``latency_data_missing``
    (whole payload) or ``latency_category_missing:<cat>`` (one category). Never
    raises and never silently skips a budgeted metric.
    """
    latency = call.get("latency")
    if not isinstance(latency, dict) or not latency:
        return [LatencyFinding("latency_data_missing", "call has no latency data")]

    # Group budgeted metrics by category, preserving order.
    by_category: dict[str, list[tuple[str, str]]] = {}
    for cat, pct, attr in _BUDGETED_METRICS:
        by_category.setdefault(cat, []).append((pct, attr))

    findings: list[LatencyFinding] = []
    for cat, metrics in by_category.items():
        data = latency.get(cat)
        num = data.get("num") if isinstance(data, dict) else None
        if not isinstance(data, dict) or not isinstance(num, (int, float)) or num <= 0:
            findings.append(LatencyFinding(
                f"latency_category_missing:{cat}",
                f"latency category {cat!r} missing or has no samples (num={num!r})"))
            continue
        for pct, attr in metrics:
            measured = data.get(pct)
            if not isinstance(measured, (int, float)):
                findings.append(LatencyFinding(
                    f"latency_category_missing:{cat}",
                    f"latency category {cat!r} missing percentile {pct!r}"))
                continue
            limit = getattr(budget, attr)
            if measured > limit:
                findings.append(LatencyFinding(
                    f"{attr}_over_budget",
                    f"{cat} {pct} {measured:.0f}ms over budget {limit}ms",
                    measured=float(measured),
                    budget=limit))
    return findings


ALL_CHECKS = [check_call_latency]


def run_all(call: dict, budget: LatencyBudget = DEFAULT_BUDGET) -> list[LatencyFinding]:
    findings: list[LatencyFinding] = []
    for check in ALL_CHECKS:
        findings.extend(check(call, budget))
    return findings
