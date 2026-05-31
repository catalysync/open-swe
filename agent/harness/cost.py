"""Per-process token + cost accumulator (Stripe/Anthropic gap: cost telemetry).

claude -p emits a final ``result`` event (stream-json) / object (json) carrying
``usage`` token counts + ``total_cost_usd``. Every node feeds those here via
``add``; bench / callers read ``snapshot`` and ``reset`` around a run. Kept
process-local and dependency-free — no DB, no reducer plumbing in graph state.
"""

from __future__ import annotations

_TOTALS = {"input_tokens": 0, "output_tokens": 0, "usd": 0.0, "calls": 0}


def add(usage: dict | None, cost_usd: float | None) -> None:
    u = usage or {}
    _TOTALS["input_tokens"] += int(u.get("input_tokens", 0) or 0)
    _TOTALS["output_tokens"] += int(u.get("output_tokens", 0) or 0)
    _TOTALS["usd"] += float(cost_usd or 0.0)
    _TOTALS["calls"] += 1


def snapshot() -> dict:
    s = dict(_TOTALS)
    s["usd"] = round(s["usd"], 4)
    return s


def reset() -> None:
    _TOTALS.update({"input_tokens": 0, "output_tokens": 0, "usd": 0.0, "calls": 0})
