"""`orchard adopt` — install the workflow into any project, for any agent.

Adoption has to be one command, because a multi-step install is a step that gets
skipped. It writes four things and nothing else:

1. ``.orchard/`` — config, gate definitions, the (empty) event log, the gitignore that
   keeps the derived index out of git and the log IN it;
2. ``docs/orchard/drivers/`` — the canonical driver plus the delta for each agent you
   asked for;
3. an **AGENTS.md** section, because that is the one instructions file more than thirty
   agents read (OpenAI Codex, Copilot, Cursor, Gemini CLI, Jules, Aider, Zed, Windsurf,
   Devin…). Claude Code reads ``CLAUDE.md``, so a pointer is written there too;
4. the MCP registration for each requested agent, in that agent's own config location.

Everything it writes is idempotent and marked with a managed-block comment, so re-running
after an upgrade updates the block and leaves your own prose alone.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

BEGIN = "<!-- ORCHARD:BEGIN (managed — edits inside this block are overwritten) -->"
END = "<!-- ORCHARD:END -->"

AGENT_TARGETS: dict[str, tuple[str, str]] = {
    # agent key -> (delta filename, mcp config path relative to repo root)
    "claude": ("claude-code.md", ".mcp.json"),
    "gemini": ("gemini-cli.md", ".gemini/settings.json"),
    "codex": ("codex-cli.md", ".codex/config.toml"),
    "copilot": ("github-copilot.md", ".vscode/mcp.json"),
    "kilo": ("kilo-cline.md", ".kilo/kilo.json"),
}

_SECTION = """{begin}
## Work queue — Orchard

This project's work is a queue of **phases** containing **tasks**, with declared
dependencies and declared file globs. It is managed by Orchard; the event log at
`.orchard/events/` is the source of truth and is committed.

**Every session starts with:**

```sh
orchard doctor && orchard recover      # is anything broken or left over from a crash?
orchard brief                          # ready work + the lessons relevant to it
```

`orchard brief` replaces reading this project's lesson and rule corpora — it retrieves
what bears on the task at hand, inside a token budget. Read it instead of them.

**To work an item:** `orchard next` → `orchard claim <ID>` → work in the worktree it
creates → `orchard gate status <ID>` and satisfy each gate → `orchard merge <ID>` →
`orchard complete <ID>`.

**Exit codes are the contract:** `0` healthy · `1` real failure · `2` could not run or
nothing to do · `3` coordination refused. **Never treat 2 as 0** — "nothing is ready"
and "everything is fine" are different facts.

**Three rules that are enforced, not merely requested:**

- A tool or reviewer that could not run is recorded `unavailable`, never `passed`.
- At least one reviewer must come from a different model family than the author.
- A bug is not closed without a regression test that fails against the unfixed code.

Full driver: [`{driver}`]({driver}) · per-agent notes: `{deltas}/`
{end}
"""


def adopt(
    repo: Path,
    agents: list[str],
    *,
    package_dir: Path,
    docs_dir: str = "docs/orchard",
    force: bool = False,
) -> list[str]:
    repo = Path(repo)
    actions: list[str] = []
    templates = package_dir.parent / "templates"

    drivers_dst = repo / docs_dir / "drivers"
    drivers_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(templates / "drivers" / "implement-phase.md", drivers_dst / "implement-phase.md")
    actions.append(f"wrote {docs_dir}/drivers/implement-phase.md")

    (drivers_dst / "deltas").mkdir(exist_ok=True)
    for key in agents:
        if key not in AGENT_TARGETS:
            raise ValueError(f"unknown agent {key!r}; known: {', '.join(AGENT_TARGETS)}")
        delta, _ = AGENT_TARGETS[key]
        shutil.copy2(templates / "drivers" / "deltas" / delta, drivers_dst / "deltas" / delta)
        actions.append(f"wrote {docs_dir}/drivers/deltas/{delta}")

    section = _SECTION.format(
        begin=BEGIN,
        end=END,
        driver=f"{docs_dir}/drivers/implement-phase.md",
        deltas=f"{docs_dir}/drivers/deltas",
    )
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = repo / name
        if name == "CLAUDE.md" and not path.exists() and "claude" not in agents:
            continue
        actions.append(_upsert_block(path, section))

    for key in agents:
        actions.append(_register_mcp(repo, key))
    return actions


def _upsert_block(path: Path, section: str) -> str:
    """Insert or replace the managed block, leaving the rest of the file untouched."""
    existing = path.read_text("utf-8") if path.exists() else ""
    if BEGIN in existing and END in existing:
        head = existing[: existing.index(BEGIN)]
        tail = existing[existing.index(END) + len(END) :]
        path.write_text(head + section.strip() + tail, "utf-8")
        return f"updated the managed block in {path.name}"
    prefix = existing.rstrip() + "\n\n" if existing.strip() else f"# {path.parent.name}\n\n"
    path.write_text(prefix + section.strip() + "\n", "utf-8")
    return f"{'appended to' if existing.strip() else 'created'} {path.name}"


def _register_mcp(repo: Path, key: str) -> str:
    """Add the Orchard MCP server to one agent's config, preserving what is there.

    Merged rather than overwritten: these files hold the user's other servers, and a
    tool that stomps them is a tool nobody runs twice.
    """
    _, rel = AGENT_TARGETS[key]
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"command": "python3", "args": ["-m", "orchard", "--repo", ".", "mcp"]}

    if rel.endswith(".toml"):
        text = path.read_text("utf-8") if path.exists() else ""
        if "[mcp_servers.orchard]" in text:
            return f"{rel} already registers orchard"
        block = (
            '\n[mcp_servers.orchard]\ncommand = "python3"\n'
            'args = ["-m", "orchard", "--repo", ".", "mcp"]\n'
        )
        path.write_text(text.rstrip() + "\n" + block if text.strip() else block.lstrip(), "utf-8")
        return f"registered orchard in {rel}"

    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8") or "{}")
        except json.JSONDecodeError:
            return f"SKIPPED {rel}: it is not valid JSON; add the server by hand"
    # Copilot/VS Code use "servers"; everyone else uses "mcpServers".
    field = "servers" if key == "copilot" else "mcpServers"
    data.setdefault(field, {})["orchard"] = entry
    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    return f"registered orchard in {rel}"
