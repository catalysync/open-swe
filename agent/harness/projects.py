"""Target-repo resolution — the harness works across many projects/stacks.

Resolution order: explicit state.project_root → config.project_root →
HARNESS_PROJECT_ROOT env → workspace root. ``_resolve_project`` matches a
project name mentioned in the task against git repos found in the workspace.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from langchain_core.runnables import RunnableConfig

WORKSPACE = os.environ.get("HARNESS_WORKSPACE", "/workspaces/codespaces-blank")


def project_root(state: dict, config: RunnableConfig) -> str:
    if state.get("project_root"):
        return state["project_root"]
    cfg = (config or {}).get("configurable", {}) or {}
    return cfg.get("project_root") or os.environ.get("HARNESS_PROJECT_ROOT") or WORKSPACE


def discover_projects() -> dict[str, str]:
    """Map project name -> abspath for git repos under the workspace (1-2 levels)."""
    found: dict[str, str] = {}
    base = Path(WORKSPACE)
    for depth1 in (base.iterdir() if base.exists() else []):
        if not depth1.is_dir() or depth1.name.startswith("."):
            continue
        if (depth1 / ".git").exists():
            found.setdefault(depth1.name, str(depth1))
        for depth2 in (depth1.iterdir() if depth1.is_dir() else []):
            if depth2.is_dir() and (depth2 / ".git").exists():
                found.setdefault(depth2.name, str(depth2))
    return found


def resolve_project(task: str) -> str | None:
    """Pick the target repo by matching a project name mentioned in the task."""
    projects = discover_projects()
    lowered = task.lower()
    for name in sorted(projects, key=len, reverse=True):  # longest wins (nagara-atlas > atlas)
        if name.lower() in lowered:
            return projects[name]
    return None


def load_rules(root: str, limit: int = 6000) -> str:
    """Load the project's house-rules catalog (AGENTS.md + CLAUDE.md) for prompts (idea #4)."""
    parts: list[str] = []
    for fname in ("AGENTS.md", "CLAUDE.md"):
        p = Path(root) / fname
        if p.is_file():
            parts.append(f"### {fname}\n{p.read_text()}")
    return "\n\n".join(parts)[:limit]


_MANIFESTS = ("requirements.txt", "pyproject.toml", "package.json", "go.mod", "Cargo.toml", "Gemfile")


def load_manifest(root: str, limit: int = 3000) -> str:
    """The project's real dependency manifest — anchors deps so the developer
    only uses libraries that actually exist (phantom-dependency prevention)."""
    for fname in _MANIFESTS:
        p = Path(root) / fname
        if p.is_file():
            return f"{fname}:\n{p.read_text()[:limit]}"
    return ""


def load_skills(root: str, limit: int = 8000) -> str:
    """Concatenate the project's .agents/skills/*.md templates for the developer prompt (ADR 0006)."""
    skills_dir = Path(root) / ".agents" / "skills"
    if not skills_dir.is_dir():
        return ""
    parts: list[str] = []
    for md in sorted(skills_dir.glob("*.md")):
        parts.append(f"### skill: {md.stem}\n{md.read_text()}")
    blob = "\n\n".join(parts)
    return blob[:limit]


# ---- long-term memory (ADR 0007): successful patterns per project ----

def _memory_path(root: str) -> Path:
    return Path(root) / ".agents" / "harness-memory.jsonl"


def append_memory(root: str, entry: dict) -> None:
    path = _memory_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def recent_memory(root: str, n: int = 5) -> str:
    path = _memory_path(root)
    if not path.exists():
        return ""
    lines = path.read_text().splitlines()[-n:]
    items = []
    for ln in lines:
        try:
            e = json.loads(ln)
            items.append(f"- {e.get('task', '')[:80]} → {e.get('summary', '')[:80]}")
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(items)


def write_skill(root: str, name: str, content: str) -> str:
    path = Path(root) / ".agents" / "skills" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return str(path)

