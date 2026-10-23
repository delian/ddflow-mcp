"""Lean Claude Code agent definitions: one per role, each naming only the tools the role needs.

A subagent that lists its tools pays for those schemas and nothing else (measured 25.4K -> 10.4K
first-turn tokens, docs/ddflow/research/lean-start.md lever 1). The definitions are templates in
`templates/claude_agents/`; `adopt` writes them to `.claude/agents/` in a managed region
(`agents/<name>`), so a re-adopt refreshes an unedited copy, a hand edit is kept and a file the
project wrote itself is left alone. Enforcement stays in hooks, leases and gates: a definition
narrows what a role is handed and states the contract, nothing more.
"""

from __future__ import annotations

from pathlib import Path

from ..infra import paths
from ..infra.fsio import Managed, RegionError
from . import harnessreg as HR

ROLES = ("researcher", "implementer", "reviewer")
#: The harness whose agent format the templates are written in.
HARNESS = "claude"
SERVER_PREFIX = "mcp__ddflow__"


def template_path(role: str) -> Path:
    return paths.templates_dir() / "claude_agents" / f"ddflow-{role}.md"


def template_text(role: str) -> str:
    return template_path(role).read_text("utf-8")


def _tools_line(role: str) -> list[str]:
    for line in template_text(role).splitlines():
        if line.startswith("tools:"):
            return [t.strip() for t in line.removeprefix("tools:").split(",") if t.strip()]
    return []


def ddflow_tools(role: str) -> list[str]:
    """The ddflow MCP tools (`ddflow_*`) the role's definition names."""
    return [t.removeprefix(SERVER_PREFIX) for t in _tools_line(role) if t.startswith(SERVER_PREFIX)]


def agents_dir(key: str = HARNESS) -> str | None:
    """Where the harness keeps subagent definitions (its descriptor's `[subagents].dir`)."""
    h = HR.get(key)
    return h.subagents.dir if h is not None and h.subagents is not None else None


def agent_files(key: str) -> dict[str, str]:
    """`{repo-relative destination: template path under templates/}` for ``key``; empty for a
    harness the templates are not written for, or one with no subagent directory."""
    base = agents_dir(key) if key == HARNESS else None
    if not base:
        return {}
    return {f"{base}/ddflow-{r}.md": f"claude_agents/ddflow-{r}.md" for r in ROLES}


def states(repo: Path) -> dict[str, str]:
    """`{path: state}` for each written definition that is not current: `edited` (a person
    changed the region), `stale` (an older ddflow's copy), `newer`, or `broken` (damaged
    markers). Missing files and files the project wrote itself are not drift. Empty for a
    project that never adopted the Claude agents."""
    out: dict[str, str] = {}
    for dst, src in agent_files(HARNESS).items():
        path = Path(repo) / dst
        if not path.is_file():
            continue
        text = path.read_text("utf-8", errors="replace")
        reg = Managed(f"agents/{Path(dst).stem}")
        try:
            if not reg.owns(text):
                continue
            state = reg.state(text)
        except RegionError:
            out[dst] = "broken"
            continue
        at = reg._region().find(text)
        _head, _, want = (paths.templates_dir() / src).read_text("utf-8").partition("\n---\n")
        current = at is not None and text[at[1] : at[2]] == want
        if state == "current" and current:
            continue
        out[dst] = "stale" if state in ("current", "older") else state
    return out


def drift_notes(repo: Path) -> list[str]:
    """The doctor note for definitions that differ from what this ddflow ships (or nothing)."""
    got = states(repo)
    if not got:
        return []
    listed = ", ".join(f"{p} ({s})" for p, s in sorted(got.items()))
    return [
        f"lean agent definitions differ from this ddflow's: {listed}; `ddflow adopt --agents claude` rewrites the managed region (a hand edit is saved beside the file)"
    ]
