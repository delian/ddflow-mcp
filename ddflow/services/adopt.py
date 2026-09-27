"""`ddflow adopt` — install the workflow into any project, for any agent.

Adoption has to be one command, because a multi-step install is a step that gets
skipped. It writes four things and nothing else:

1. ``.ddflow/`` — config, gate definitions, the (empty) event log, the gitignore that
   keeps the derived index out of git and the log IN it;
2. ``docs/ddflow/drivers/`` — the canonical driver plus the delta for each agent you
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
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..infra import paths

BEGIN = "<!-- DDFLOW:BEGIN (managed — edits inside this block are overwritten) -->"
END = "<!-- DDFLOW:END -->"

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
    "cursor": ".cursor/rules/ddflow.mdc",
}

# The per-project text, deliberately SHORT.
#
# When ddflow is reached over MCP, the tool descriptions already carry the how: what
# each call does, what its arguments mean, what an exit code means. Repeating that here
# would be a second copy that drifts from the first, and the first is the one the model
# actually reads at call time. So this block carries only what a tool description
# cannot: that this project HAS a queue, that you must claim before you edit, and the
# three rules that are enforced rather than requested.
#
# Projects that drive ddflow from a shell instead get the same block plus a pointer to
# the full driver, which is where the long form lives.
_SECTION = """{begin}
## Work queue — ddflow

Work in this project is a queue of **phases** containing **tasks**, with declared
dependencies and declared file globs. It is managed by ddflow. The event log in
`.ddflow/events/` is the source of truth and is committed; everything else is derived.

**Start every session with `ddflow_brief`** (MCP) or `ddflow brief` (shell). It
returns any work left over from a crash, what is ready now, why everything else is
blocked, and the past lessons relevant to the task — and it replaces reading this
project's lesson and rule files.

**Claim before you edit.** `ddflow_claim` leases the item and gives you an isolated
git worktree. An unclaimed edit can be destroyed by a parallel agent.

Then: `ddflow_next` → `ddflow_claim` → work in the worktree → `ddflow_gate_status`
and satisfy each gate → `ddflow_merge` → `ddflow_complete`.

**Four rules are enforced, not requested:**

- A tool or reviewer that could not run is recorded `unavailable`, never `passed`.
- Every gate in the pipeline must carry SOME outcome before an item completes. Silence
  is not a pass; `ddflow gate skip <id> <gate> --reason "..."` is the way past one.
- At least one reviewer must come from a different model family than the author.
- A bug is not closed without a regression test that fails against the unfixed code.

**Record as you go:** the operator's words verbatim (`ddflow session prompt`), a
decision when it is settled (`ddflow decision add --globs ...`), a bug when you find it
and before you fix it, a lesson after any surprise. `ddflow replay` rebuilds this
project from those; a summary rebuilds the summary.

**Before anything non-trivial:** `ddflow recall "<what you are about to do>"`.

**Companion tools the gates expect** — `ddflow companions` says which are present and
what installing each would run: `roborev`, `codeguide-mcp`, `context7`, and a memory
server. When one is missing, **propose it to the operator early** — what it buys, what
it would run — and install it yourself if they agree. Never install without asking, and
when they decline, record that gate `unavailable` rather than passing it unaided.

**Exit codes:** `0` fine · `1` failure · `2` could not run / nothing to do · `3`
refused. Never treat `2` as `0`.

{driver_line}
{end}
"""

_DRIVER_LINE = "Full driver: [`{driver}`]({driver}) · per-agent notes: `{deltas}/`"


def project_section(docs_dir: str = "docs/ddflow") -> str:
    """The managed block this version of ddflow would write.

    Factored out of `adopt` so `rules_status` can compare what is ON DISK against what
    SHOULD be there. Two generators would be two answers to the same question, and the
    drift between them would be invisible in exactly the file that tells an agent how to
    behave.
    """
    return _SECTION.format(
        begin=BEGIN,
        end=END,
        driver_line=_DRIVER_LINE.format(
            driver=f"{docs_dir}/drivers/implement-phase.md",
            deltas=f"{docs_dir}/drivers/deltas",
        ),
    )


#: The rules surfaces whose state `rules_status` reports. `AGENTS.md` is the one more than
#: thirty agents read; `CLAUDE.md` only counts once it exists, because writing it into a
#: project that does not use Claude Code would be noise.
RULES_FILES = ("AGENTS.md", "CLAUDE.md")

MISSING, NO_BLOCK, STALE, CURRENT = "missing", "no_block", "stale", "current"
#: A native rule that EXISTS and does not bind. `alwaysApply: false` makes Cursor load the
#: file only when it feels like it, which for claim-before-you-edit is the same as not
#: having it — and strictly worse than stale TEXT, because the text is fine and inert.
#: Reported at the severity of missing, not of drifted.
NOT_BINDING = "not_binding"


@dataclass
class RulesState:
    """One rules file, and whether it still says what this ddflow would say."""

    path: str
    state: str

    @property
    def needs_attention(self) -> bool:
        return self.state != CURRENT

    def render(self) -> str:
        return {
            MISSING: f"{self.path} does not exist — the agent has no project rules at all",
            NOT_BINDING: (
                f"{self.path} exists but does not bind (`alwaysApply` is not true), so the "
                f"agent may never load it"
            ),
            NO_BLOCK: f"{self.path} exists but its ddflow section was removed",
            STALE: f"{self.path}'s ddflow section is from an older version and has drifted",
            CURRENT: f"{self.path} is current",
        }[self.state]


#: Frontmatter that makes a Cursor project rule BIND. `alwaysApply: true`, because
#: claim-before-you-edit is not a rule that should depend on the model choosing to load it.
_NATIVE_FRONTMATTER = (
    "---\n"
    "description: >-\n"
    "  How work is queued, claimed and reviewed in this project. Read before\n"
    "  starting any task, and before editing any file.\n"
    "globs:\n"
    "alwaysApply: true\n"
    "---\n\n"
)


def native_rule_text(docs_dir: str = "docs/ddflow") -> str:
    """The exact content of an agent's NATIVE rules file.

    The same managed block as `AGENTS.md`, with the markers stripped and binding
    frontmatter added — so the two copies cannot say different things. Factored out for the
    same reason `project_section` was: the checker has to compare against what the writer
    writes, and two generators would be two answers.
    """
    body = project_section(docs_dir).replace(BEGIN, "").replace(END, "").strip()
    return _NATIVE_FRONTMATTER + body + "\n"


def adopted_agents(repo: Path, *, docs_dir: str = "docs/ddflow") -> list[str]:
    """Which agents this project was adopted FOR, read from the driver deltas on disk.

    Nothing records the list — `adopt` takes it as an argument and writes per-agent files.
    The deltas are therefore the only durable trace, and they are what tells the checker
    whether `.cursor/rules/ddflow.mdc` is expected here or would be noise.
    """
    deltas = Path(repo) / docs_dir / "drivers" / "deltas"
    if not deltas.is_dir():
        return []
    present = {p.name for p in deltas.glob("*.md")}
    return sorted(key for key, (delta, _mcp) in AGENT_TARGETS.items() if delta in present)


def has_been_adopted(repo: Path, *, docs_dir: str = "docs/ddflow") -> bool:
    """Has `adopt` ever run here — as distinct from `init`?

    The distinction matters and `.ddflow/config.toml` cannot make it: `init` writes that file
    too. So a project that only ran `init` has no `AGENTS.md`, CORRECTLY, and reporting its
    absence would be reporting a rules file that was never asked for — on every tool call.
    The drivers are the marker, because writing them is the first thing `adopt` does.
    """
    return (Path(repo) / docs_dir / "drivers" / "implement-phase.md").is_file()


def rules_status(repo: Path, *, docs_dir: str = "docs/ddflow") -> list[RulesState]:
    """Is every agent-facing rules file present and CURRENT?

    Nothing checked this before. `adopt` writes the block idempotently and then nobody
    looks again — so a deleted `AGENTS.md`, a block someone stripped, or a block written by
    an older ddflow all left the agent reading rules that were absent, incomplete or wrong,
    with every surface reporting the project as adopted because `.ddflow/config.toml`
    existed. Adoption is judged by a config file; the INSTRUCTIONS are a separate fact.

    `CLAUDE.md` is reported only if it already exists: creating it in a project that does
    not use Claude Code would be noise, and its absence is not a defect.
    """
    # An UNADOPTED project has no rules file and should not be told it is missing one: that
    # is what `setup_todo` and the handshake's adoption offer are for. Reporting it here
    # would put "AGENTS.md does not exist" in front of an operator who has not asked for
    # AGENTS.md — which is noise, and noise on every call.
    if not has_been_adopted(repo, docs_dir=docs_dir):
        return []
    out: list[RulesState] = []
    want = project_section(docs_dir).strip()
    for name in RULES_FILES:
        path = Path(repo) / name
        if not path.exists():
            if name != "AGENTS.md":
                continue  # not a defect — see the docstring
            out.append(RulesState(name, MISSING))
            continue
        text = path.read_text("utf-8", errors="replace")
        if BEGIN not in text or END not in text:
            out.append(RulesState(name, NO_BLOCK))
            continue
        block = text[text.index(BEGIN) : text.index(END) + len(END)].strip()
        out.append(RulesState(name, CURRENT if block == want else STALE))

    # The NATIVE surfaces, which for some agents OUTRANK `AGENTS.md` and are therefore what
    # actually binds. Cursor's precedence is Team Rules > Project Rules > User Rules >
    # `.cursorrules` > `AGENTS.md`, so a project adopted for Cursor with a missing or drifted
    # `.cursor/rules/ddflow.mdc` has an agent that does not follow the rules — while
    # `AGENTS.md` sits there current and every check reported the project as fine. This was
    # the whole point of writing the native file and it was the one file nobody verified.
    #
    # No BEGIN/END markers here: the frontmatter has to be first for the rule to bind, so
    # the file is ddflow's entirely and `no_block` cannot apply. Missing, drifted, current.
    want_native = native_rule_text(docs_dir)
    for key in adopted_agents(repo, docs_dir=docs_dir):
        rel = NATIVE_RULES.get(key)
        if not rel:
            continue
        path = Path(repo) / rel
        if not path.is_file():
            out.append(RulesState(rel, MISSING))
            continue
        got = path.read_text("utf-8", errors="replace")
        if got == want_native:
            out.append(RulesState(rel, CURRENT))
        elif not re.search(r"^alwaysApply:\s*true\s*$", got, re.M):
            # Checked BEFORE `stale`, because it is the more serious fault and a file that
            # does not bind is usually also textually different. Drifted text still gets
            # read; a rule with `alwaysApply: false` may never be loaded at all.
            out.append(RulesState(rel, NOT_BINDING))
        else:
            out.append(RulesState(rel, STALE))
    return out


def adopt(
    repo: Path,
    agents: list[str],
    *,
    package_dir: Path | None = None,
    docs_dir: str = "docs/ddflow",
    force: bool = False,
    install_hooks: bool = True,
    launch: str = "auto",
    image: str = "ghcr.io/OWNER/ddflow:latest",
) -> list[str]:
    repo = Path(repo)
    actions: list[str] = []
    # Templates live INSIDE the package (`ddflow/templates/`), not beside it, so they
    # ship in the wheel. They were originally a sibling directory, which worked from a
    # source checkout and then raised FileNotFoundError for every installed user --
    # the classic packaging bug that only a real install reproduces.
    templates = Path(package_dir) / "templates" if package_dir else paths.templates_dir()
    if not (templates / "drivers" / "implement-phase.md").is_file():
        raise FileNotFoundError(
            f"driver templates are missing from {templates}. This is a packaging fault, "
            f"not a configuration one: reinstall ddflow-mcp."
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

    section = project_section(docs_dir)
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = repo / name
        if name == "CLAUDE.md" and not path.exists() and "claude" not in agents:
            continue
        actions.append(_upsert_block(path, section))

    if install_hooks:
        from ..services.enforce import install as install_hook

        actions.append(install_hook(repo))
    for key in agents:
        actions.append(_register_mcp(repo, key, launch=launch, image=image))
        if key in NATIVE_RULES:
            actions.append(_write_native_rule(repo, key, docs_dir))
    return actions


def _write_native_rule(repo: Path, key: str, docs_dir: str = "docs/ddflow") -> str:
    """Write the agent's own rules file, for agents whose native surface outranks
    `AGENTS.md`.

    The body is the SAME managed block, so there is one source for the project text and
    the two copies cannot say different things. Only the frontmatter differs, and it is
    what makes the rule bind: `alwaysApply: true`, because claim-before-you-edit is not
    a rule that should depend on the model choosing to load it.
    """
    path = repo / NATIVE_RULES[key]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(native_rule_text(docs_dir), "utf-8")
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


#: The module an agent spawns to get the MCP server, and the directory that must be on
#: `PYTHONPATH` for it to import.
#:
#: Both used to be hardcoded relative to THIS file (`parents[1]`, `ddflow.mcp_server`)
#: and both broke silently when the package was split into layers: the server spawned,
#: could not import, printed nothing, and every client waiting on its handshake hung for
#: as long as it was willing to wait. Derived now — the module name is checked against
#: the import system by `tests/test_packaging.py`, and the parent comes from the
#: package's own `__file__` rather than from counting directory levels above a file that
#: may move again.
MCP_MODULE = "ddflow.surfaces.mcp"


def _package_parent() -> str:
    """The directory containing the `ddflow` package — what goes on PYTHONPATH."""
    from ..infra.paths import package_parent

    return str(package_parent())


def _launch_entry(
    launch: str = "auto", image: str = "ghcr.io/OWNER/ddflow:latest"
) -> dict[str, object]:
    """How an MCP client should spawn ddflow.

    ``docker`` is the zero-toolchain option: the operator needs Docker and nothing
    else, and it behaves identically on Linux, macOS and Windows. The flags are not
    decoration —

    * ``-i`` but **never** ``-t``: MCP is newline-delimited JSON-RPC over stdin/stdout,
      and a TTY injects control sequences that corrupt the stream.
    * ``--rm``: one container per session; the state lives in the mounted repo.
    * ``-v <repo>:/repo``: the repo is the only durable thing; everything ddflow
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
        and not shutil.which("ddflow-mcp")
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
        pkg_parent = _package_parent()
        return {
            "command": sys.executable,
            "args": ["-m", MCP_MODULE],
            "env": {"PYTHONPATH": pkg_parent},
        }
    if shutil.which("uvx") and not _running_from_source():
        return {"command": "uvx", "args": ["ddflow-mcp"]}
    if shutil.which("ddflow-mcp") and not _running_from_source():
        return {"command": "ddflow-mcp", "args": []}
    # Source checkout: point at THIS tree, so a developer's project uses the code they
    # are editing.
    pkg_parent = _package_parent()
    return {
        "command": sys.executable,
        "args": ["-m", MCP_MODULE],
        "env": {"PYTHONPATH": pkg_parent},
    }


def _running_from_source() -> bool:
    """True when this module lives in a checkout rather than in site-packages."""
    here = Path(__file__).resolve()
    return not any(part in ("site-packages", "dist-packages") for part in here.parts)


def _register_mcp(
    repo: Path, key: str, *, launch: str = "auto", image: str = "ghcr.io/OWNER/ddflow:latest"
) -> str:
    """Add the ddflow MCP server to one agent's config, preserving what is there.

    Merged rather than overwritten: these files hold the user's other servers, and a
    tool that stomps them is a tool nobody runs twice.
    """
    _, rel = AGENT_TARGETS[key]
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    # `uvx` fetches and runs the published package in an ephemeral environment, so a
    # project adopting ddflow needs no clone, no virtualenv, no PYTHONPATH and no
    # install step an operator can forget. When ddflow is running from a source
    # checkout rather than an installed distribution, fall back to that checkout --
    # otherwise developing ddflow would silently configure the project against the
    # PUBLISHED version instead of the one under test.
    entry = _launch_entry(launch, image)

    if rel.endswith(".toml"):
        text = path.read_text("utf-8") if path.exists() else ""
        if "[mcp_servers.ddflow]" in text:
            return f"{rel} already registers ddflow"
        args = ", ".join(f'"{a}"' for a in entry.get("args", []))
        block = f'\n[mcp_servers.ddflow]\ncommand = "{entry["command"]}"\nargs = [{args}]\n'
        path.write_text(text.rstrip() + "\n" + block if text.strip() else block.lstrip(), "utf-8")
        return f"registered ddflow in {rel}"

    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8") or "{}")
        except json.JSONDecodeError:
            return f"SKIPPED {rel}: it is not valid JSON; add the server by hand"
    # Copilot/VS Code use "servers"; everyone else uses "mcpServers".
    field = "servers" if key == "copilot" else "mcpServers"
    data.setdefault(field, {})["ddflow"] = entry
    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    return f"registered ddflow in {rel}"
