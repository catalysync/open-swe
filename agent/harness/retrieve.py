"""Context pre-hydration (Stripe Toolshed gap: pre-hydrate likely context).

Dependency-free retrieval: rank existing repo files by keyword/symbol overlap
with the task and return a compact "relevant files" block to inject into the
planner + developer prompts, so the agent starts grounded in real code instead
of guessing. A future upgrade swaps the grep ranking for embeddings (ChromaDB).
"""

from __future__ import annotations

import re
from pathlib import Path

_SKIP_DIRS = {".git", "node_modules", "dist", "build", ".venv", "venv",
              "__pycache__", ".next", "target", ".mypy_cache", ".ruff_cache"}
_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".rb", ".go", ".rs", ".vue",
         ".svelte", ".java", ".css", ".scss", ".sql"}
_STOP = {"the", "and", "for", "with", "add", "use", "that", "this", "from",
         "into", "make", "new", "function", "feature", "code", "file", "test"}
_MAX_FILES = 2000
_MAX_READ = 20000
_TOP = 8


def _keywords(task: str) -> set[str]:
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", task)}
    return {w for w in words if w not in _STOP}


def pre_hydrate(root: str, task: str, *, top: int = _TOP) -> str:
    base = Path(root)
    kws = _keywords(task)
    if not base.is_dir() or not kws:
        return ""
    scored: list[tuple[int, str]] = []
    seen = 0
    for path in base.rglob("*"):
        if seen >= _MAX_FILES:
            break
        if not path.is_file() or path.suffix.lower() not in _EXTS:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        seen += 1
        rel = str(path.relative_to(base))
        name_hits = sum(2 for k in kws if k in rel.lower())
        try:
            body = path.read_text(errors="ignore")[:_MAX_READ].lower()
        except Exception:  # noqa: BLE001
            continue
        body_hits = sum(body.count(k) for k in kws)
        score = name_hits * 5 + body_hits
        if score:
            scored.append((score, rel))
    if not scored:
        return ""
    scored.sort(reverse=True)
    return "\n".join(f"- {rel}" for _, rel in scored[:top])
