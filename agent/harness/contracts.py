"""Typed inter-node contracts (ADR 0005 / LangGraph nanodegree).

Reasoning nodes emit JSON; we validate it into a Pydantic model so routing
runs on a validated field (e.g. ``review.status``), not a substring guess.
On a parse/validation failure we ask claude -p once to repair the JSON, then
fall back to a safe default — so the pipeline stays predictable.
"""

from __future__ import annotations

import json
import re
from typing import Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

from .claude import claude_text

T = TypeVar("T", bound=BaseModel)


class ReviewResult(BaseModel):
    status: Literal["APPROVED", "NEEDS_REVISION"]
    findings: list[str] = Field(default_factory=list)


def _extract_json(text: str) -> str | None:
    """Pull the first JSON object out of an LLM response (handles ``` fences/prose)."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        return fence.group(1)
    brace = re.search(r"\{.*\}", text, re.S)
    return brace.group(0) if brace else None


def parse_or_repair(raw: str, model: type[T], *, default: T) -> T:
    """Validate ``raw`` into ``model``; repair once via claude -p; else ``default``."""
    candidate = _extract_json(raw)
    if candidate:
        try:
            return model.model_validate_json(candidate)
        except ValidationError:
            pass
    repaired = claude_text(
        "Convert the text below into JSON that EXACTLY matches this schema. "
        "Output ONLY the JSON, nothing else.\n\nSchema:\n"
        f"{json.dumps(model.model_json_schema())}\n\nText:\n{raw[:4000]}"
    )
    candidate = _extract_json(repaired)
    if candidate:
        try:
            return model.model_validate_json(candidate)
        except ValidationError:
            pass
    return default
