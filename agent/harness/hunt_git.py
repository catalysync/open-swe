"""Git / PR helpers for the `hunt` graph (kept out of hunt.py for the LOC cap)."""

from __future__ import annotations

import os
import re
import subprocess

_EXCLUDE = (":(exclude).agents/**", ":(exclude)**/*.db", ":(exclude)**/*.jsonl")


def sh(args: list[str], root: str, *, token_unset: bool = False, timeout: int = 120) -> tuple[bool, str]:
    env = dict(os.environ)
    if token_unset:
        env.pop("GITHUB_TOKEN", None)
    try:
        r = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def complexity_hotspots(root: str, *, min_rank: str = "C", limit: int = 25) -> str:
    """radon cc → compact list of high-branch functions to target for coverage.
    Falls back to '' if radon isn't available, so the prompt still drives it."""
    ok, out = sh(["radon", "cc", "-s", "-n", min_rank, ".",
                  "-e", "**/node_modules/*,**/.venv/*,**/dist/*"], root, timeout=90)
    if not ok or not out.strip():
        return ""
    lines = [ln.strip() for ln in out.splitlines() if " - " in ln and "(" in ln]
    return "\n".join(lines[:limit])


def slug(text: str) -> str:
    first = next((ln for ln in text.splitlines() if ln.strip()), text)
    s = re.sub(r"[^a-z0-9]+", "-", first.lower()).strip("-")
    return (s[:40] or "hunt").rstrip("-")


def opted_in(config: dict | None) -> bool:
    cfg = (config or {}).get("configurable", {}) or {}
    return bool(cfg.get("open_pr") or os.environ.get("HARNESS_OPEN_PR") == "1")


def commit_and_pr(root: str, branch: str, base: str, subject: str, body: str,
                  *, do_pr: bool) -> str:
    """Stage (minus scratch) → commit → optionally push + open PR into base."""
    sh(["git", "add", "--", ".", *_EXCLUDE], root)
    ok_c, _ = sh(["git", "commit", "-m", subject], root)
    if not ok_c:
        return f"nothing to commit on `{branch}`."
    if not do_pr:
        return f"committed `{branch}` (PRs disabled)."
    ok_p, po = sh(["git", "push", "-u", "origin", branch], root, token_unset=True)
    if not ok_p:
        return f"push failed: {po[:200]}"
    ok_pr, pro = sh(["gh", "pr", "create", "--base", base, "--head", branch,
                     "--title", subject, "--body", body], root, token_unset=True)
    if ok_pr and pro.strip():
        return f"PR: {pro.strip().splitlines()[-1]}"
    return f"pushed `{branch}`; open PR manually."
