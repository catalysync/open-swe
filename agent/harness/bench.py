"""Benchmark + A/B compare for prompt/architecture changes (ADR 0012).

Runs a fixed task suite through the harness on throwaway git repos, records
objective metrics the graph already emits (gate pass/fail, reviewer verdict,
retries, outcome) + a composite score, tagged by a version label. Compare two
tags to see whether a prompt change helped or regressed.

    python -m agent.harness.bench run <tag>        # e.g. the prompts git SHA
    python -m agent.harness.bench compare <a> <b>
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from langchain_core.messages import HumanMessage

from . import cost
from .graph import build_harness_graph

_OUT = Path(tempfile.gettempdir()) / "harness-bench"

# (name, seed files, task) — each runs on a fresh git repo
SUITE: list[tuple[str, dict[str, str], str]] = [
    ("py-add-fn",
     {"calc.py": "def mul(a, b):\n    return a * b\n",
      "test_calc.py": "from calc import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"},
     "Add a subtract(a, b) function to calc.py that returns a-b, with a test."),
    ("py-validate",
     {"svc.py": "def price_for(qty, unit):\n    return qty * unit\n"},
     "Add input validation to price_for in svc.py: reject non-numeric or "
     "negative qty/unit with a ValueError, and add tests covering the errors."),
]


def _seed_repo(files: dict[str, str]) -> str:
    d = tempfile.mkdtemp(prefix="bench-")
    for name, body in files.items():
        (Path(d) / name).write_text(body)
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "-c", "user.email=b@b.co", "-c", "user.name=b",
                    "commit", "-qm", "init"], cwd=d, check=True)
    return d


def _score(state: dict) -> int:
    """Composite 0-100: outcome + gates − retries."""
    v = state.get("validation") or {}
    review_ok = (state.get("review") or {}).get("status") == "APPROVED"
    s = 0
    if state.get("status") == "done":
        s += 50
    if v.get("tests_passed", False):
        s += 20
    if v.get("lint_passed", False) and v.get("types_passed", True) and v.get("security_passed", True):
        s += 15
    if review_ok:
        s += 15
    s -= 5 * int(state.get("retry_count", 0))
    return max(0, min(100, s))


def _run_one(name: str, files: dict[str, str], task: str) -> dict:
    root = _seed_repo(files)
    graph = build_harness_graph(hitl=False)
    cost.reset()
    t0 = time.monotonic()
    try:
        final = graph.invoke(
            {"messages": [HumanMessage(content=task)]},
            config={"configurable": {"project_root": root}, "recursion_limit": 9999},
        )
    except Exception as exc:  # noqa: BLE001
        c = cost.snapshot()
        return {"task": name, "error": str(exc)[:300], "score": 0,
                "usd": c["usd"], "tokens": c["input_tokens"] + c["output_tokens"]}
    v = final.get("validation") or {}
    review = final.get("review") or {}
    c = cost.snapshot()
    return {
        "task": name,
        "status": final.get("status"),
        "retries": final.get("retry_count", 0),
        "review": review.get("status"),
        "n_findings": len(review.get("findings", [])),
        "lint": v.get("lint_passed"),
        "security": v.get("security_passed"),
        "tests": v.get("tests_passed"),
        "wall_s": round(time.monotonic() - t0, 1),
        "score": _score(final),
        "usd": c["usd"],
        "tokens": c["input_tokens"] + c["output_tokens"],
    }


def run(tag: str) -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    rows = [_run_one(*case) for case in SUITE]
    path = _OUT / f"{tag}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    avg = sum(r["score"] for r in rows) / len(rows)
    total_usd = sum(r.get("usd", 0) for r in rows)
    print(f"tag={tag}  avg_score={avg:.1f}  total=${total_usd:.3f}  → {path}")
    for r in rows:
        print(f"  {r['task']:14} score={r['score']:3}  status={r.get('status')}  "
              f"retries={r.get('retries')}  {r.get('wall_s')}s  "
              f"${r.get('usd', 0):.3f}  {r.get('tokens', 0)}tok")


def _load(tag: str) -> dict[str, dict]:
    path = _OUT / f"{tag}.jsonl"
    return {json.loads(ln)["task"]: json.loads(ln) for ln in path.read_text().splitlines()}


def compare(a: str, b: str) -> None:
    ra, rb = _load(a), _load(b)
    print(f"{'task':14} {a:>8} {b:>8}  delta")
    for name in sorted(set(ra) | set(rb)):
        sa = ra.get(name, {}).get("score", 0)
        sb = rb.get(name, {}).get("score", 0)
        print(f"{name:14} {sa:8} {sb:8}  {sb - sa:+d}")
    avg_a = sum(r["score"] for r in ra.values()) / max(len(ra), 1)
    avg_b = sum(r["score"] for r in rb.values()) / max(len(rb), 1)
    print(f"{'AVG':14} {avg_a:8.1f} {avg_b:8.1f}  {avg_b - avg_a:+.1f}")


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "run":
        run(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "compare":
        compare(sys.argv[2], sys.argv[3])
    else:
        print("usage: bench.py run <tag> | compare <tagA> <tagB>")


if __name__ == "__main__":
    main()
