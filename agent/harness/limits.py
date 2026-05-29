"""Built-in structural limits — file length, function length, cyclomatic complexity.

Runs inside the deterministic gate (validator._run_structural) so EVERY graph that
calls run_gate (build, mine, hunt) enforces the same ceilings the harness itself
lives under. Keeps generated code small + simple instead of letting an agent ship
a 600-line module with a 40-branch function that still passes lint/tests.

Scope: only files CHANGED vs HEAD (+ untracked) in this run, so pre-existing legacy
debt never blocks a fix. File-length is language-agnostic. Per-function length +
cyclomatic complexity use radon for Python and lizard for everything else
(TS/JS/Go/Rust/Ruby), so the same caps cover backend and frontend.

Thresholds default to file=200 / function=60 / complexity=10 and are overridable
per-project via `.agents/limits.toml`:

    max_file_lines = 200
    max_function_lines = 60
    max_complexity = 10
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

_DEFAULTS = {"max_file_lines": 200, "max_function_lines": 60, "max_complexity": 10}
_SRC_EXT = {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".rb", ".go", ".rs"}
_SKIP_PARTS = {".agents", "node_modules", ".venv", "dist", "build", "__pycache__",
               ".turbo", "migrations", "alembic", ".egg-info"}


def _load_thresholds(root: Path) -> dict:
    cfg = dict(_DEFAULTS)
    f = root / ".agents" / "limits.toml"
    if f.is_file():
        try:
            data = tomllib.loads(f.read_text())
            cfg.update({k: int(v) for k, v in data.items() if k in _DEFAULTS})
        except Exception:  # noqa: BLE001 — bad config falls back to defaults
            pass
    return cfg


def _git(args: list[str], root: Path) -> list[str]:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30)
        return [ln for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []
    except Exception:  # noqa: BLE001
        return []


def _changed_source_files(root: Path, base: str = "HEAD") -> list[Path]:
    """Files touched in this run (diff vs base + staged + untracked). Empty list
    when not a git repo → caller skips (we never scan a whole legacy tree)."""
    names = set(_git(["diff", "--name-only", base], root))
    names |= set(_git(["diff", "--name-only", "--cached"], root))
    names |= set(_git(["ls-files", "--others", "--exclude-standard"], root))
    out: list[Path] = []
    for name in names:
        p = root / name
        if p.suffix not in _SRC_EXT or not p.is_file():
            continue
        if any(part in _SKIP_PARTS or part.endswith(".egg-info") for part in p.parts):
            continue
        out.append(p)
    return out


def _check_file_lines(files: list[Path], root: Path, cap: int) -> list[str]:
    errs = []
    for p in files:
        try:
            n = sum(1 for _ in p.open("rb"))
        except Exception:  # noqa: BLE001
            continue
        if n > cap:
            errs.append(f"{p.relative_to(root)} is {n} lines (cap {cap}). Split it.")
    return errs


def _radon_blocks(files: list[Path], root: Path) -> list[tuple[str, dict]]:
    py = [str(p) for p in files if p.suffix in (".py", ".pyi")]
    if not py or not shutil.which("radon"):
        return []
    try:
        r = subprocess.run(["radon", "cc", "-j", *py], cwd=root,
                           capture_output=True, text=True, timeout=120)
        data = json.loads(r.stdout or "{}")
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[str, dict]] = []
    for fname, blocks in data.items():
        rel = str(Path(fname))
        out.extend((rel, b) for b in (blocks if isinstance(blocks, list) else []))
    return out


def _lizard_blocks(files: list[Path], root: Path) -> list[tuple[str, dict]]:
    """Non-Python source via lizard (TS/JS/Go/Rust/Ruby). Normalised to the same
    {name, lineno, endline, complexity} shape radon emits."""
    others = [p for p in files if p.suffix not in (".py", ".pyi")]
    if not others:
        return []
    try:
        import lizard  # noqa: PLC0415 — optional dep, lazy so the gate degrades gracefully
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[str, dict]] = []
    for p in others:
        try:
            info = lizard.analyze_file(str(p))
        except Exception:  # noqa: BLE001
            continue
        rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
        for fn in info.function_list:
            out.append((rel, {"name": fn.name, "lineno": fn.start_line,
                              "endline": fn.end_line, "complexity": fn.cyclomatic_complexity}))
    return out


def _complexity_blocks(files: list[Path], root: Path) -> list[tuple[str, dict]]:
    return _radon_blocks(files, root) + _lizard_blocks(files, root)


def _check_complexity(files: list[Path], root: Path, max_cc: int, max_fn: int) -> list[str]:
    errs = []
    for rel, b in _complexity_blocks(files, root):
        loc, name = f"{rel}:{b.get('lineno')}", b.get("name")
        cc = b.get("complexity", 0)
        if cc > max_cc:
            errs.append(f"{loc} {name} complexity {cc} (cap {max_cc}). Simplify it.")
        length = (b.get("endline", 0) or 0) - (b.get("lineno", 0) or 0) + 1
        if length > max_fn:
            errs.append(f"{loc} {name} is {length} lines (cap {max_fn}). Split it.")
    return errs


def hotspots(root: Path, base: str = "HEAD", limit: int = 15) -> str:
    """Compact, highest-complexity-first summary of functions in the changed files
    — fed to the reviewer so it scrutinises the gnarly code, not just the diff."""
    files = _changed_source_files(root, base)
    blocks = sorted(_complexity_blocks(files, root),
                    key=lambda rb: rb[1].get("complexity", 0), reverse=True)
    lines = [f"{rel}:{b.get('lineno')} {b.get('name')} — CC {b.get('complexity')}"
             for rel, b in blocks[:limit] if b.get("complexity", 0) >= 6]
    return "\n".join(lines)


def check_limits(root: Path) -> tuple[bool, list[str]]:
    """Returns (passed, errors). Empty/clean when no changed source files."""
    files = _changed_source_files(root)
    if not files:
        return True, []
    t = _load_thresholds(root)
    errs = _check_file_lines(files, root, t["max_file_lines"])
    errs += _check_complexity(files, root, t["max_complexity"], t["max_function_lines"])
    if errs:
        return False, [f"limits[{len(errs)}]:\n" + "\n".join(f"  • {e}" for e in errs)]
    return True, []
