"""Deterministic evaluation gate — ADR 0003 Layer 1 (no LLM).

Runs the project's OWN standard tool-set when it declares one, else falls back
to per-stack defaults. Resolution ladder (see gates.py):
  tier 1  .agents/gates.toml          — project declares its gate commands
  tier 2  task-runner convention      — just / make / npm aggregate-or-granular
  tier 3  per-stack defaults (here)   — for bare repos with no declared gate

Principle (Stripe Minions): "what's good for humans is good for agents" — prefer
the same command a human/CI runs over a guessed one, so the agent can't pass a
gate the harness invented while the project's real gate goes unrun. Missing
toolchains are skipped, not failed, so the gate never blocks on an absent tool.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .gates import resolve_project_gates


def _run(cmd: list[str], cwd: str, timeout: int = 300) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _detect_stack(root: Path) -> str:
    if (root / "Gemfile").exists():
        return "ruby"
    if (root / "go.mod").exists():
        return "go"
    if (root / "Cargo.toml").exists():
        return "rust"
    if (root / "package.json").exists():
        try:
            pkg = json.loads((root / "package.json").read_text())
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        except Exception:  # noqa: BLE001
            deps = {}
        if "next" in deps:
            return "nextjs"
        if any(d.startswith("@nestjs") for d in deps):
            return "nestjs"
        return "node"
    if (root / "pyproject.toml").exists() or any(root.glob("*.py")):
        return "python"
    return "unknown"


# stack -> (lint_cmd, test_cmd, security_cmd) ; None entries are skipped (ADR 0003)
_GATES: dict[str, tuple[list[str] | None, list[str] | None, list[str] | None]] = {
    "python": (["ruff", "check", "."], ["pytest", "-q", "--no-header"], ["bandit", "-r", ".", "-q"]),
    "ruby": (["rubocop"], ["rspec"], ["brakeman", "-q", "--no-pager"]),
    "go": (["go", "vet", "./..."], ["go", "test", "-race", "./..."], ["gosec", "./..."]),
    "rust": (["cargo", "clippy", "--quiet"], ["cargo", "test", "--quiet"], ["cargo", "audit"]),
    "node": (["npx", "eslint", "."], ["npx", "vitest", "run"], ["npx", "semgrep", "--error", "--config=auto"]),
    "nextjs": (["npx", "eslint", "."], ["npx", "vitest", "run"], ["npx", "semgrep", "--error", "--config=auto"]),
    "nestjs": (["npx", "eslint", "."], ["npx", "jest"], ["npx", "semgrep", "--error", "--config=auto"]),
}

# stack -> type-check command (ADR 0003 Layer 1 "Types"). Skipped if tool absent;
# go/rust type-check via their compilers (vet/clippy already cover that surface).
_TYPES: dict[str, list[str]] = {
    "python": ["mypy", "."],
    "ruby": ["srb", "tc"],
    "node": ["npx", "tsc", "--noEmit"],
    "nextjs": ["npx", "tsc", "--noEmit"],
    "nestjs": ["npx", "tsc", "--noEmit"],
}

# stack -> autofix commands run BEFORE the lint gate (Stripe Minions autofix).
# Best-effort: failures are ignored, they only resolve mechanically-fixable issues.
_FIXERS: dict[str, list[list[str]]] = {
    "python": [["ruff", "check", "--fix", "."], ["ruff", "format", "."]],
    "ruby": [["rubocop", "-A"]],
    "go": [["gofmt", "-w", "."]],
    "rust": [["cargo", "clippy", "--fix", "--allow-dirty", "--allow-no-vcs"]],
    "node": [["npx", "eslint", ".", "--fix"]],
    "nextjs": [["npx", "eslint", ".", "--fix"]],
    "nestjs": [["npx", "eslint", ".", "--fix"]],
}


def _autofix(stack: str, root: str) -> bool:
    """Apply mechanical fixes in-place; returns True if any fixer ran."""
    ran = False
    for cmd in _FIXERS.get(stack, []):
        if shutil.which(cmd[0]):
            ran = True
            _run(cmd, root)
    return ran


def _run_structural(root: Path) -> tuple[bool, list[str]]:
    """ADR 0006: run the project's .agents/validators/*.py AST/structural checks.

    Each validator script exits non-zero (with stdout/stderr explaining) when the
    generated code violates a required structure. Absent dir = no structural gate.
    """
    vdir = root / ".agents" / "validators"
    if not vdir.is_dir():
        return True, []
    errs: list[str] = []
    for script in sorted(vdir.glob("*.py")):
        ok, out = _run(["python", str(script)], str(root), timeout=120)
        if not ok:
            errs.append(f"structural[{script.stem}]:\n{out}")
    return (not errs), errs


def _run_declared(
    root: Path, stack: str, autofixed: bool, source: str, gates: list[tuple[str, list[str]]]
) -> dict | None:
    """Run the project's OWN gate commands. None if no command's tool is on PATH
    (so the caller falls back to per-stack defaults rather than a false green)."""
    errors: list[str] = []
    detail: list[dict] = []
    ok_all = True
    for name, argv in gates:
        if not argv or not shutil.which(argv[0]):
            continue
        ok, out = _run(argv, str(root), timeout=600)
        detail.append({"name": name, "passed": ok})
        if not ok:
            ok_all = False
            errors.append(f"gate[{name}]:\n{out}")
    if not detail:
        return None  # nothing ran → let stack defaults handle it

    structural_passed, structural_errs = _run_structural(root)
    errors.extend(structural_errs)
    passed = ok_all and structural_passed
    # Mirror the granular keys (= overall) so downstream _val_ok/bench are unchanged.
    return {
        "stack": stack,
        "gate_source": source,
        "gates": detail,
        "autofixed": autofixed,
        "lint_passed": passed,
        "types_passed": passed,
        "structural_passed": structural_passed,
        "security_passed": passed,
        "tests_passed": passed,
        "passed": passed,
        "errors": errors,
        "ran": True,
    }


def run_gate(project_root: str) -> dict:
    root = Path(project_root)
    stack = _detect_stack(root)

    # tier 1/2: the project's declared or conventional standard tool-set wins.
    autofixed = _autofix(stack, project_root)
    resolved = resolve_project_gates(root)
    if resolved is not None:
        source, gates = resolved
        declared = _run_declared(root, stack, autofixed, source, gates)
        if declared is not None:
            return declared

    # tier 3: per-stack defaults (fallback for bare repos).
    lint_cmd, test_cmd, sec_cmd = _GATES.get(stack, (None, None, None))

    errors: list[str] = []
    lint_passed = types_passed = tests_passed = security_passed = True
    ran = False

    if lint_cmd and shutil.which(lint_cmd[0]):
        ran = True
        ok, out = _run(lint_cmd, project_root)
        lint_passed = ok
        if not ok:
            errors.append(f"{lint_cmd[0]}:\n{out}")

    type_cmd = _TYPES.get(stack)
    if type_cmd and shutil.which(type_cmd[0]):
        ran = True
        ok, out = _run(type_cmd, project_root)
        types_passed = ok
        if not ok:
            errors.append(f"types[{type_cmd[-1] if type_cmd[0] == 'npx' else type_cmd[0]}]:\n{out}")

    structural_passed, structural_errs = _run_structural(root)
    if structural_errs:
        ran = True
        errors.extend(structural_errs)

    if sec_cmd and shutil.which(sec_cmd[0]):
        ran = True
        ok, out = _run(sec_cmd, project_root)
        security_passed = ok
        if not ok:
            errors.append(f"security[{sec_cmd[0]}]:\n{out}")

    if test_cmd and shutil.which(test_cmd[0]):
        ran = True
        ok, out = _run(test_cmd, project_root)
        tests_passed = ok
        if not ok:
            errors.append(f"{test_cmd[0]}:\n{out}")

    passed = lint_passed and types_passed and structural_passed and security_passed and tests_passed
    return {
        "stack": stack,
        "gate_source": "stack-default",
        "autofixed": autofixed,
        "lint_passed": lint_passed,
        "types_passed": types_passed,
        "structural_passed": structural_passed,
        "security_passed": security_passed,
        "tests_passed": tests_passed,
        "passed": passed,
        "errors": errors,
        "ran": ran,
    }
