"""Tier-3 per-stack defaults for the evaluation gate (see validator.py).

Used only when a project declares no gate via .agents/gates.toml or a task-runner
convention. Stack is detected from marker files; each stack maps to lint/test/
security commands, a type-checker, and autofixers. Missing tools are skipped.
"""

from __future__ import annotations

import json
from pathlib import Path


def detect_stack(root: Path) -> str:
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
GATES: dict[str, tuple[list[str] | None, list[str] | None, list[str] | None]] = {
    "python": (["ruff", "check", "."], ["pytest", "-q", "--no-header"], ["bandit", "-r", ".", "-q", "--skip", "B101"]),
    "ruby": (["rubocop"], ["rspec"], ["brakeman", "-q", "--no-pager"]),
    "go": (["go", "vet", "./..."], ["go", "test", "-race", "./..."], ["gosec", "./..."]),
    "rust": (["cargo", "clippy", "--quiet"], ["cargo", "test", "--quiet"], ["cargo", "audit"]),
    "node": (["npx", "eslint", "."], ["npx", "vitest", "run"], ["npx", "semgrep", "--error", "--config=auto"]),
    "nextjs": (["npx", "eslint", "."], ["npx", "vitest", "run"], ["npx", "semgrep", "--error", "--config=auto"]),
    "nestjs": (["npx", "eslint", "."], ["npx", "jest"], ["npx", "semgrep", "--error", "--config=auto"]),
}

# stack -> type-check command (ADR 0003 Layer 1 "Types"). Skipped if tool absent;
# go/rust type-check via their compilers (vet/clippy already cover that surface).
TYPES: dict[str, list[str]] = {
    "python": ["mypy", "."],
    "ruby": ["srb", "tc"],
    "node": ["npx", "tsc", "--noEmit"],
    "nextjs": ["npx", "tsc", "--noEmit"],
    "nestjs": ["npx", "tsc", "--noEmit"],
}

# stack -> autofix commands run BEFORE the lint gate (Stripe Minions autofix).
# Best-effort: failures are ignored, they only resolve mechanically-fixable issues.
FIXERS: dict[str, list[list[str]]] = {
    "python": [["ruff", "check", "--fix", "."], ["ruff", "format", "."]],
    "ruby": [["rubocop", "-A"]],
    "go": [["gofmt", "-w", "."]],
    "rust": [["cargo", "clippy", "--fix", "--allow-dirty", "--allow-no-vcs"]],
    "node": [["npx", "eslint", ".", "--fix"]],
    "nextjs": [["npx", "eslint", ".", "--fix"]],
    "nestjs": [["npx", "eslint", ".", "--fix"]],
}
