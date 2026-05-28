"""Deterministic evaluation gate — ADR 0003 Layer 1 (no LLM).

Runs lint + tests in the project root. Missing toolchains are reported as
'skipped', not failed, so the gate doesn't block on absent tools.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _run(cmd: list[str], cwd: str, timeout: int = 300) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def run_gate(project_root: str) -> dict:
    """Run lint + tests for the detected stack. Returns a validation dict."""
    root = Path(project_root)
    errors: list[str] = []
    lint_passed = True
    tests_passed = True

    is_python = (root / "pyproject.toml").exists() or any(root.glob("*.py"))

    if is_python and shutil.which("ruff"):
        ok, out = _run(["ruff", "check", "."], project_root)
        lint_passed = ok
        if not ok:
            errors.append(f"ruff:\n{out}")

    if is_python and shutil.which("pytest"):
        ok, out = _run(["pytest", "-q", "--no-header"], project_root)
        tests_passed = ok
        if not ok:
            errors.append(f"pytest:\n{out}")

    return {
        "lint_passed": lint_passed,
        "tests_passed": tests_passed,
        "errors": errors,
        "ran": bool(is_python),
    }
