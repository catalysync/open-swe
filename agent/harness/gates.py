"""Project-gate discovery (Stripe Minions: "what's good for humans is good for
agents" — run the project's OWN standard tool-set, don't guess per-stack).

Resolution ladder, most-explicit first:
  tier 1  .agents/gates.toml          — the project declares its gate commands
  tier 2  task-runner convention      — just / make / npm aggregate-or-granular
  (tier 3 — per-stack defaults — lives in validator.py as the fallback)

Returns an ordered list of (name, argv). validator.py executes them; this module
only reads files, so it stays side-effect-free and engine-generic.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import tomllib
from pathlib import Path

# Aggregate targets bundle lint+type+test (prefer one); else the granular set.
_AGGREGATE = ("check", "verify", "ci")
_GRANULAR = ("lint", "typecheck", "types", "test", "smoke")

_RECIPE = re.compile(r"(?m)^([a-zA-Z][\w-]*)(?:\s+[^\n:]*)?:(?!=)")


def _pick(names: set[str]) -> list[str]:
    for agg in _AGGREGATE:
        if agg in names:
            picked = [agg]
            if "smoke" in names:  # smoke is usually a separate run-level gate
                picked.append("smoke")
            return picked
    return [g for g in _GRANULAR if g in names]


def _from_manifest(root: Path) -> list[tuple[str, list[str]]] | None:
    p = root / ".agents" / "gates.toml"
    if not p.is_file():
        return None
    try:
        data = tomllib.loads(p.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    cmds = [(k, shlex.split(v)) for k, v in data.items() if isinstance(v, str) and v.strip()]
    return cmds or None


def _from_convention(root: Path) -> list[tuple[str, list[str]]] | None:
    jf = next((root / n for n in ("justfile", "Justfile", ".justfile") if (root / n).is_file()), None)
    if jf and shutil.which("just"):
        names = set(_RECIPE.findall(jf.read_text()))
        picked = _pick(names)
        if picked:
            return [(g, ["just", g]) for g in picked]

    pj = root / "package.json"
    if pj.is_file() and shutil.which("npm"):
        try:
            scripts = set(json.loads(pj.read_text()).get("scripts", {}))
        except (OSError, json.JSONDecodeError):
            scripts = set()
        picked = _pick(scripts)
        if picked:
            return [(g, ["npm", "run", g]) for g in picked]

    mk = next((root / n for n in ("Makefile", "makefile") if (root / n).is_file()), None)
    if mk and shutil.which("make"):
        names = set(_RECIPE.findall(mk.read_text()))
        picked = _pick(names)
        if picked:
            return [(g, ["make", g]) for g in picked]

    return None


def resolve_project_gates(root: Path) -> tuple[str, list[tuple[str, list[str]]]] | None:
    """Return (source, [(name, argv), ...]) or None when nothing is declared.

    source is "declared" (tier 1) or "convention" (tier 2). None → caller uses
    its per-stack defaults (tier 3)."""
    declared = _from_manifest(root)
    if declared:
        return "declared", declared
    conv = _from_convention(root)
    if conv:
        return "convention", conv
    return None
