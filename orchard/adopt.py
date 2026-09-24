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
    "cursor": ("cursor.md", ".cursor/mcp.json"),
}

#: Agents whose NATIVE rules surface outranks `AGENTS.md`, and where writing only
#: AGENTS.md would therefore be unreliable. Cursor's precedence is
#: Team Rules > Project Rules > User Rules > .cursorrules > AGENTS.md, so a project
#: rule is what actually binds; AGENTS.md is written too, as the fallback it is.
NATIVE_RULES: dict[str, str] = {
    "cursor": ".cursor/rules/orchard.mdc",
}

# The per-project text, deliberately SHORT.
#
# When Orchard is reached over MCP, the tool descriptions already carry the how: what
# each call does, what its arguments mean, what an exit code means. Repeating that here
# would be a second copy that drifts from the first, and the first is the one the model
# actually reads at call time. So this block carries only what a tool description
# cannot: that this project HAS a queue, that you must claim before you edit, and the
# three rules that are enforced rather than requested.
#
# Projects that drive Orchard from a shell instead get the same block plus a pointer to
# the full driver, which is where the long form lives.
_SECTION = """{begin}
## Work queue — Orchard

Work in this project is a queue of **phases** containing **tasks**, with declared
dependencies and declared file globs. It is managed by Orchard. The event log in
`.orchard/events/` is the source of truth and is committed; everything else is derived.

**Start every session with `orchard_brief`** (MCP) or `orchard brief` (shell). It
returns any work left over from a crash, what is ready now, why everything else is
blocked, and the past lessons relevant to the task — and it replaces reading this
project's lesson and rule files.

**Claim before you edit.** `orchard_claim` leases the item and gives you an isolated
git worktree. An unclaimed edit can be destroyed by a parallel agent.

Then: `orchard_next` → `orchard_claim` → work in the worktree → `orchard_gate_status`
and satisfy each gate → `orchard_merge` → `orchard_complete`.

**Four rules are enforced, not requested:**

- A tool or reviewer that could not run is recorded `unavailable`, never `passed`.
- Every gate in the pipeline must carry SOME outcome before an item completes. Silence
  is not a pass; `orchard gate skip <id> <gate> --reason "..."` is the way past one.
- At least one reviewer must come from a different model family than the author.
- A bug is not closed without a regression test that fails against the unfixed code.

**Record as you go:** the operator's words verbatim (`orchard session prompt`), a
decision when it is settled (`orchard decision add --globs ...`), a bug when you find it
and before you fix it, a lesson after any surprise. `orchard replay` rebuilds this
project from those; a summary rebuilds the summary.

**Before anything non-trivial:** `orchard recall "<what you are about to do>"`.

**Companion tools the gates expect** — `orchard companions` says which are present:
`roborev`, `codeguide-mcp`, `context7`, and a memory server. When one is absent, record
its gate `unavailable`.

**Exit codes:** `0` fine · `1` failure · `2` could not run / nothing to do · `3`
refused. Never treat `2` as `0`.

{driver_line}
{end}
"""

_DRIVER_LINE = "Full driver: [`{driver}`]({driver}) · per-agent notes: `{deltas}/`"


def adopt(
    repo: Path,
    agents: list[str],
    *,
    package_dir: Path | None = None,
    docs_dir: str = "docs/orchard",
    force: bool = False,
    install_hooks: bool = True,
    launch: str = "auto",
    image: str = "ghcr.io/OWNER/orchard:latest",
) -> list[str]:
    repo = Path(repo)
    actions: list[str] = []
    # Templates live INSIDE the package (`orchard/templates/`), not beside it, so they
    # ship in the wheel. They were originally a sibling directory, which worked from a
    # source checkout and then raised FileNotFoundError for every installed user --
    # the classic packaging bug that only a real install reproduces.
    templates = Path(package_dir or Path(__file__).resolve().parent) / "templates"
    if not (templates / "drivers" / "implement-phase.md").is_file():
        raise FileNotFoundError(
            f"driver templates are missing from {templates}. This is a packaging fault, "
            f"not a configuration one: reinstall orchard-mcp."
        )

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
        driver_line=_DRIVER_LINE.format(
            driver=f"{docs_dir}/drivers/implement-phase.md",
            deltas=f"{docs_dir}/drivers/deltas",
        ),
    )
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = repo / name
        if name == "CLAUDE.md" and not path.exists() and "claude" not in agents:
            continue
        actions.append(_upsert_block(path, section))

    if install_hooks:
        from .enforce import install as install_hook

        actions.append(install_hook(repo))
    for key in agents:
        actions.append(_register_mcp(repo, key, launch=launch, image=image))
        if key in NATIVE_RULES:
            actions.append(_write_native_rule(repo, key, section))
    return actions


def _write_native_rule(repo: Path, key: str, section: str) -> str:
    """Write the agent's own rules file, for agents whose native surface outranks
    `AGENTS.md`.

    The body is the SAME managed block, so there is one source for the project text and
    the two copies cannot say different things. Only the frontmatter differs, and it is
    what makes the rule bind: `alwaysApply: true`, because claim-before-you-edit is not
    a rule that should depend on the model choosing to load it.
    """
    path = repo / NATIVE_RULES[key]
    path.parent.mkdir(parents=True, exist_ok=True)
    body = section.replace(BEGIN, "").replace(END, "").strip()
    frontmatter = (
        "---\n"
        "description: >-\n"
        "  How work is queued, claimed and reviewed in this project. Read before\n"
        "  starting any task, and before editing any file.\n"
        "globs:\n"
        "alwaysApply: true\n"
        "---\n\n"
    )
    path.write_text(frontmatter + body + "\n", "utf-8")
    return f"wrote {NATIVE_RULES[key]} (always-applied project rule)"


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


def _launch_entry(
    launch: str = "auto", image: str = "ghcr.io/OWNER/orchard:latest"
) -> dict[str, object]:
    """How an MCP client should spawn Orchard.

    ``docker`` is the zero-toolchain option: the operator needs Docker and nothing
    else, and it behaves identically on Linux, macOS and Windows. The flags are not
    decoration —

    * ``-i`` but **never** ``-t``: MCP is newline-delimited JSON-RPC over stdin/stdout,
      and a TTY injects control sequences that corrupt the stream.
    * ``--rm``: one container per session; the state lives in the mounted repo.
    * ``-v <repo>:/repo``: the repo is the only durable thing; everything Orchard
      writes goes there.
    * ``--add-host=host.docker.internal:host-gateway``: lets a reviewer endpoint served
      on the operator's own machine be reachable. Docker Desktop provides the name
      already; on Linux it does not exist without this flag.
    """
    import shutil
    import sys

    if launch == "docker" or (
        launch == "auto"
        and not shutil.which("uvx")
        and not shutil.which("orchard-mcp")
        and shutil.which("docker")
    ):
        return {
            "command": "docker",
            "args": [
                "run",
                "-i",
                "--rm",
                "-v",
                "${workspaceFolder}:/repo",
                "--add-host=host.docker.internal:host-gateway",
                image,
            ],
        }
    if launch == "python":
        pkg_parent = str(Path(__file__).resolve().parents[1])
        return {
            "command": sys.executable,
            "args": ["-m", "orchard.mcp_server"],
            "env": {"PYTHONPATH": pkg_parent},
        }
    if shutil.which("uvx") and not _running_from_source():
        return {"command": "uvx", "args": ["orchard-mcp"]}
    if shutil.which("orchard-mcp") and not _running_from_source():
        return {"command": "orchard-mcp", "args": []}
    # Source checkout: point at THIS tree, so a developer's project uses the code they
    # are editing.
    pkg_parent = str(Path(__file__).resolve().parents[1])
    return {
        "command": sys.executable,
        "args": ["-m", "orchard.mcp_server"],
        "env": {"PYTHONPATH": pkg_parent},
    }


def _running_from_source() -> bool:
    """True when this module lives in a checkout rather than in site-packages."""
    here = Path(__file__).resolve()
    return not any(part in ("site-packages", "dist-packages") for part in here.parts)


def _register_mcp(
    repo: Path, key: str, *, launch: str = "auto", image: str = "ghcr.io/OWNER/orchard:latest"
) -> str:
    """Add the Orchard MCP server to one agent's config, preserving what is there.

    Merged rather than overwritten: these files hold the user's other servers, and a
    tool that stomps them is a tool nobody runs twice.
    """
    _, rel = AGENT_TARGETS[key]
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    # `uvx` fetches and runs the published package in an ephemeral environment, so a
    # project adopting Orchard needs no clone, no virtualenv, no PYTHONPATH and no
    # install step an operator can forget. When Orchard is running from a source
    # checkout rather than an installed distribution, fall back to that checkout --
    # otherwise developing Orchard would silently configure the project against the
    # PUBLISHED version instead of the one under test.
    entry = _launch_entry(launch, image)

    if rel.endswith(".toml"):
        text = path.read_text("utf-8") if path.exists() else ""
        if "[mcp_servers.orchard]" in text:
            return f"{rel} already registers orchard"
        args = ", ".join(f'"{a}"' for a in entry.get("args", []))
        block = f'\n[mcp_servers.orchard]\ncommand = "{entry["command"]}"\nargs = [{args}]\n'
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
