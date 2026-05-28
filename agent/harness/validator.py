"""Deterministic evaluation gate — ADR 0003 Layer 1, multi-stack (no LLM).

Detects the stack from marker files and runs the matching lint + test
toolchain (ADR 0003 table). Missing toolchains are reported 'skipped', not
failed, so the gate never blocks on an absent tool.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


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


# stack -> (lint_cmd, test_cmd) ; None entries are skipped
_GATES: dict[str, tuple[list[str] | None, list[str] | None]] = {
    "python": (["ruff", "check", "."], ["pytest", "-q", "--no-header"]),
    "ruby": (["rubocop"], ["rspec"]),
    "go": (["go", "vet", "./..."], ["go", "test", "./..."]),
    "rust": (["cargo", "clippy", "--quiet"], ["cargo", "test", "--quiet"]),
    "node": (["npx", "eslint", "."], ["npx", "vitest", "run"]),
    "nextjs": (["npx", "eslint", "."], ["npx", "vitest", "run"]),
    "nestjs": (["npx", "eslint", "."], ["npx", "jest"]),
}


def run_gate(project_root: str) -> dict:
    root = Path(project_root)
    stack = _detect_stack(root)
    lint_cmd, test_cmd = _GATES.get(stack, (None, None))

    errors: list[str] = []
    lint_passed = tests_passed = True
    ran = False

    if lint_cmd and shutil.which(lint_cmd[0]):
        ran = True
        ok, out = _run(lint_cmd, project_root)
        lint_passed = ok
        if not ok:
            errors.append(f"{lint_cmd[0]}:\n{out}")

    if test_cmd and shutil.which(test_cmd[0]):
        ran = True
        ok, out = _run(test_cmd, project_root)
        tests_passed = ok
        if not ok:
            errors.append(f"{test_cmd[0]}:\n{out}")

    return {
        "stack": stack,
        "lint_passed": lint_passed,
        "tests_passed": tests_passed,
        "errors": errors,
        "ran": ran,
    }
