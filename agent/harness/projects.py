"""Target-repo resolution — the harness works across many projects/stacks.

Resolution order: explicit state.project_root → config.project_root →
HARNESS_PROJECT_ROOT env → workspace root. ``_resolve_project`` matches a
project name mentioned in the task against git repos found in the workspace.
"""

from __future__ import annotations

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
