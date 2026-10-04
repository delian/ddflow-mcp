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
from typing import Any

from ..infra import paths
from . import install_info as _INSTALL

#: Where an operator reads the manual step for an agent with no project config.
_DOCS_HINT = "docs/ddflow/drivers/deltas/"

BEGIN = "<!-- DDFLOW:BEGIN (managed — edits inside this block are overwritten) -->"
END = "<!-- DDFLOW:END -->"

#: How a server entry nests inside an agent's config file. A VOCABULARY rather than a
#: chain of `if key == ...`: the writer used to special-case Copilot inline, which worked
#: for exactly two shapes and could not express a third. Each value is checked against
#: that agent's own documentation — see `docs/RESEARCH.md` R15.
SHAPE_MCP_SERVERS = "mcpServers"  #: {"mcpServers": {"ddflow": {command, args}}} -- common
SHAPE_SERVERS = "servers"  #: {"servers": {"ddflow": {type: "stdio", command, args}}} -- VS Code
SHAPE_COPILOT = "copilot"  #: {"mcpServers": {"ddflow": {type: "local", ..., tools}}} -- Copilot CLI
SHAPE_MCP_DOT_SERVERS = "mcp.servers"  #: {"mcp": {"servers": {...}}} -- ZCode (GLM)
SHAPE_OPENCODE = "opencode"  #: {"mcp": {"ddflow": {type: "local", command: [...]}}}
SHAPE_TOML = "toml.mcp_servers"  #: [mcp_servers.ddflow] in TOML -- Codex
#: No project-level MCP config file EXISTS for this agent: it is configured in an IDE
#: panel, a web UI, or a user-level file outside the repository. The delta doc and
#: `AGENTS.md` still apply, and those are the parts that make the workflow portable — so
#: the agent is SUPPORTED, and the honest record is that one step is manual. Inventing a
#: plausible path would be worse than admitting it: ddflow would write a file the agent
#: never reads and the operator would believe it was wired up.
SHAPE_NONE = "none"


@dataclass(frozen=True)
class AgentTarget:
    """One supported harness: its delta doc, its MCP config file, and that file's shape.

    One record per agent rather than parallel dicts keyed by the same string. Two dicts
    that must be edited together are the drift this project keeps paying for -- there
    were three of them before this (a ternary in the adopter, `_json_field` in the
    companions writer, and a `SERVERS_FIELD_AGENTS` set beside it), and the comment on
    the last one correctly predicted that a third shape would break them.
    """

    delta: str  #: filename under `templates/drivers/deltas/`
    config: str  #: MCP config path relative to the repo root; "" when none exists
    shape: str  #: one of the SHAPE_* constants above

    # There is deliberately NO `rules` field here. One briefly existed, populated for six
    # agents and read by nothing: `NATIVE_RULES` already owns which surface an agent reads
    # and `adopt` is driven entirely by that, so the field was a second, unowned copy of the
    # same fact -- `NATIVE_RULES["tabnine"].path` said `.tabnine/guidelines/ddflow.md` while
    # it said `.tabnine/guidelines/`. The next maintainer would have filled it in believing
    # `adopt` honoured it. Found by roborev on 3040d4b; the duplicate-then-drift class.

    @property
    def writes_config(self) -> bool:
        return bool(self.config) and self.shape != SHAPE_NONE


#: Every harness ddflow can adopt a project into. Each path and shape is taken from that
#: product's OWN documentation (`docs/RESEARCH.md` R15) -- never from the family
#: resemblance between them, because a wrong key is valid JSON that the agent silently
#: ignores, which looks exactly like success.
AGENT_TARGETS: dict[str, AgentTarget] = {
    "claude": AgentTarget("claude-code.md", ".mcp.json", SHAPE_MCP_SERVERS),
    "gemini": AgentTarget("gemini-cli.md", ".gemini/settings.json", SHAPE_MCP_SERVERS),
    "codex": AgentTarget("codex-cli.md", ".codex/config.toml", SHAPE_TOML),
    # GitHub Copilot's OWN surface, separate from VS Code's. The CLI searches upward for
    # `.mcp.json` and also reads `.github/mcp.json`, which is the one meant to be
    # committed and shared, so that is the one written. Its entries carry `type: "local"`
    # and a `tools` allowlist. This used to point at `.vscode/mcp.json`, which is VS
    # Code's file and is now the `vscode` target -- adopt BOTH to cover both surfaces.
    "copilot": AgentTarget("github-copilot.md", ".github/mcp.json", SHAPE_COPILOT),
    # VS Code's built-in MCP support, which any VS Code agent uses -- not Copilot-specific.
    # Top-level key is `servers`, NOT `mcpServers`, and entries name their transport.
    "vscode": AgentTarget("vscode.md", ".vscode/mcp.json", SHAPE_SERVERS),
    # Kilo Code. Its CLI is an opencode fork and reads opencode's shape: `mcp`, NOT
    # `mcpServers`. This was SHAPE_MCP_SERVERS until a probe against Kilo 7.2.20 showed
    # that file listing "No MCP servers configured" -- valid JSON, silently ignored.
    "kilo": AgentTarget("kilo-cline.md", ".kilo/kilo.json", SHAPE_OPENCODE),
    "cursor": AgentTarget("cursor.md", ".cursor/mcp.json", SHAPE_MCP_SERVERS),
    # Kimi Code CLI. Project-level `.kimi-code/mcp.json` takes precedence over the
    # user-level copy. NOT a repo-root `.mcp.json`: secondary write-ups say it reuses
    # Claude's file and the official docs do not, so only the official docs count.
    "kimi": AgentTarget("kimi-code.md", ".kimi-code/mcp.json", SHAPE_MCP_SERVERS),
    # opencode. `command` is ONE array including the arguments, and `enabled` is explicit.
    "opencode": AgentTarget("opencode.md", "opencode.json", SHAPE_OPENCODE),
    # ZCode, Zhipu's coding agent and how GLM is driven. Nests under `mcp` -> `servers`.
    "glm": AgentTarget("zcode-glm.md", ".zcode/config.json", SHAPE_MCP_DOT_SERVERS),
    # Qwen Code CLI, a Gemini CLI fork: same settings shape, its own directory. Its
    # default context file is QWEN.md, and it reads AGENTS.md when present.
    "qwen": AgentTarget("qwen-code.md", ".qwen/settings.json", SHAPE_MCP_SERVERS),
    # Google Antigravity. Workspace MCP is `.agents/mcp_config.json`; rules may be
    # AGENTS.md, GEMINI.md, or `.agents/rules/`.
    "antigravity": AgentTarget("antigravity.md", ".agents/mcp_config.json", SHAPE_MCP_SERVERS),
    # Devin CLI. `.devin/mcp_config.json` is the git-tracked project scope (a
    # `.local.json` sibling exists for secrets and is gitignored). CLOUD Devin sessions
    # are configured in the web UI instead, which no repo file can do.
    "devin": AgentTarget("devin.md", ".devin/mcp_config.json", SHAPE_MCP_SERVERS),
    # Qodo Command reads an `mcp.json` at the project root. The Qodo Gen IDE plugin keeps
    # its own per-user config, which is the manual half named in the delta.
    "qodo": AgentTarget("qodo.md", "mcp.json", SHAPE_MCP_SERVERS),
    # Tabnine Agent. Project scope SHALLOW-MERGES over user and system scopes.
    "tabnine": AgentTarget("tabnine.md", ".tabnine/agent/settings.json", SHAPE_MCP_SERVERS),
    # --- Supported, but with NO project-level MCP file to write. -------------------
    # Each of these is a verified absence, not an unresearched gap: the delta doc says
    # where the operator must add the server by hand, and AGENTS.md still carries the
    # workflow.
    #
    # Aider has no MCP client support at all, and no AGENTS.md convention -- it loads a
    # read-only context file named by `read:` in `.aider.conf.yml`.
    "aider": AgentTarget("aider.md", "", SHAPE_NONE),
    # Cline's MCP settings are a single GLOBAL file; its project surface is rules only.
    "cline": AgentTarget("cline.md", "", SHAPE_NONE),
    # Windsurf/Cascade is now Devin Desktop; its docs state a global config only.
    "windsurf": AgentTarget("windsurf.md", "", SHAPE_NONE),
    # Replit configures MCP entirely in the web UI, and its instruction file is replit.md.
    "replit": AgentTarget("replit.md", "", SHAPE_NONE),
    # OpenHands' primary path is Settings -> MCP in the UI. A `config.toml` `[mcp]`
    # `stdio_servers` array still exists and its own docs call it development-only, so
    # it is documented in the delta rather than written here.
    "openhands": AgentTarget("openhands.md", "", SHAPE_NONE),
    # Goose keeps extensions in a user-level YAML; its project surface is `.goosehints`,
    # and it reads AGENTS.md as well.
    "goose": AgentTarget("goose.md", "", SHAPE_NONE),
    # Sourcegraph Cody is Enterprise-only since 2025-07-23 and is configured through the
    # editor's own settings, under a `cody.mcpServers` key rather than a repo file.
    "cody": AgentTarget("cody.md", "", SHAPE_NONE),
}


#: How a native rules surface must be written.
#:
#: `FORM_WHOLE` — the file is ddflow's entirely, because something must come FIRST in it
#:   (Cursor's `.mdc` binds through YAML frontmatter, which cannot be preceded by a marker).
#: `FORM_BLOCK` — a managed BEGIN/END block inside a file the project may already own, so
#:   the operator's own rules survive alongside ours.
#: `FORM_AIDER` — not a rules file at all: a `read:` entry in `.aider.conf.yml`, because
#:   Aider discovers nothing automatically and only loads what it is told to load.
FORM_WHOLE = "whole"
FORM_BLOCK = "block"
FORM_AIDER = "aider-conf"


@dataclass(frozen=True)
class NativeRule:
    """One agent's own instruction surface, and how to write into it."""

    path: str  #: relative to the repo root
    form: str  #: FORM_WHOLE | FORM_BLOCK | FORM_AIDER
    why: str  #: why `AGENTS.md` alone is not enough here — shown by `doctor`


#: Agents whose OWN instruction surface must also carry the block, because `AGENTS.md`
#: alone does not reach them.
#:
#: **The block is INLINED into each, never pointed at.** A pointer was the obvious design
#: and this repo had already refuted it: *"a link is only followed if the agent chooses to
#: follow it"* (`templates/drivers/deltas/kilo-cline.md`). A one-line stub saying "see
#: AGENTS.md" is therefore a rule that binds only if the model feels like opening a file,
#: which is exactly the property an enforced rule must not have.
#:
#: Inlining means N copies of one text, and the answer to that is the answer already used
#: for `AGENTS.md` and `CLAUDE.md`: ONE generator (`project_section`), a managed block, and
#: `rules_status()` comparing every copy against it so drift is reported rather than
#: discovered. Duplication that a check owns is not duplication that drifts.
#:
#: Absent from this map means the agent reads `AGENTS.md` directly — which is most of them,
#: and is the whole reason `AGENTS.md` is the canonical surface.
NATIVE_RULES: dict[str, NativeRule] = {
    # Precedence is Team Rules > Project Rules > User Rules > .cursorrules > AGENTS.md, so
    # a project rule is what actually binds and AGENTS.md is the fallback it is.
    "cursor": NativeRule(
        ".cursor/rules/ddflow.mdc",
        FORM_WHOLE,
        "Cursor ranks project rules ABOVE AGENTS.md, so AGENTS.md alone is outranked",
    ),
    # QWEN.md is Qwen Code's default context file. It reads AGENTS.md when present, but the
    # default is what an unconfigured checkout uses.
    "qwen": NativeRule(
        "QWEN.md", FORM_BLOCK, "QWEN.md is Qwen Code's DEFAULT context file, not AGENTS.md"
    ),
    # Cline's project surface is a rules directory; its MCP config is global-only.
    "cline": NativeRule(
        ".clinerules/ddflow.md", FORM_BLOCK, "Cline reads .clinerules/, not AGENTS.md"
    ),
    "tabnine": NativeRule(
        ".tabnine/guidelines/ddflow.md",
        FORM_BLOCK,
        "Tabnine Agent reads .tabnine/guidelines/*.md, not AGENTS.md",
    ),
    # Replit's own convention, and it must be at the project root.
    "replit": NativeRule(
        "replit.md", FORM_BLOCK, "Replit reads replit.md at the project root, not AGENTS.md"
    ),
    # Goose reads AGENTS.md *and* .goosehints by default; the hints file is the one that is
    # committed and the one CONTEXT_FILE_NAMES cannot silently drop.
    "goose": NativeRule(
        ".goosehints", FORM_BLOCK, "Goose reads .goosehints as well, and it is committed"
    ),
    # Aider auto-discovers NOTHING. Without a `read:` entry it never sees the rules at all,
    # which makes this the most load-bearing entry in the map.
    "aider": NativeRule(
        ".aider.conf.yml",
        FORM_AIDER,
        "Aider loads only what `read:` names — it discovers no instruction file at all",
    ),
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
        # `not_binding` means "present and will not take effect", and the REASON differs by
        # surface. It used to render Cursor's reason for every one of them, so an operator
        # whose `.aider.conf.yml` was missing its `read:` entry was told that `alwaysApply`
        # was not true — about a file that has no such key. A message that names the wrong
        # cause is worse than a generic one: it sends the reader to fix something that is
        # not broken.
        if self.state == NOT_BINDING:
            return f"{self.path} exists but does not bind: {self.not_binding_reason}"
        return {
            MISSING: f"{self.path} does not exist — the agent has no project rules at all",
            NO_BLOCK: f"{self.path} exists but its ddflow section was removed",
            STALE: f"{self.path}'s ddflow section is from an older version and has drifted",
            CURRENT: f"{self.path} is current",
        }[self.state]

    @property
    def not_binding_reason(self) -> str:
        """Why this surface will not take effect, in its OWN terms."""
        if self.path.endswith(".aider.conf.yml"):
            return (
                f"it does not list `{AIDER_READS}` under `read:`, and Aider loads no "
                f"instruction file it was not told to load"
            )
        return "`alwaysApply` is not true, so the agent may never load it"


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
    return sorted(key for key, t in AGENT_TARGETS.items() if t.delta in present)


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
    want_block = project_section(docs_dir).strip()
    for key in adopted_agents(repo, docs_dir=docs_dir):
        rule = NATIVE_RULES.get(key)
        if rule is None:
            continue  # reads AGENTS.md directly, which the loop above already checked
        path = Path(repo) / rule.path
        if not path.is_file():
            out.append(RulesState(rule.path, MISSING))
            continue
        got = path.read_text("utf-8", errors="replace")
        if rule.form == FORM_AIDER:
            # Not a rules file: the question is whether Aider is TOLD to load AGENTS.md.
            listed = _aider_lists_agents_md(got)
            out.append(RulesState(rule.path, CURRENT if listed else NOT_BINDING))
            continue
        if rule.form == FORM_BLOCK:
            if BEGIN not in got or END not in got:
                out.append(RulesState(rule.path, NO_BLOCK))
                continue
            block = got[got.index(BEGIN) : got.index(END) + len(END)].strip()
            out.append(RulesState(rule.path, CURRENT if block == want_block else STALE))
            continue
        if got == want_native:
            out.append(RulesState(rule.path, CURRENT))
        elif not re.search(r"^alwaysApply:\s*true\s*$", got, re.M):
            # Checked BEFORE `stale`, because it is the more serious fault and a file that
            # does not bind is usually also textually different. Drifted text still gets
            # read; a rule with `alwaysApply: false` may never be loaded at all.
            out.append(RulesState(rule.path, NOT_BINDING))
        else:
            out.append(RulesState(rule.path, STALE))
    return out


def starter_config() -> str:
    """The single configuration file.

    One file, not two. An earlier version also wrote `.ddflow/gates.toml` carrying a
    placeholder `unit_tests.command`, and because gates.toml wins over config.toml that
    placeholder silently overrode anything `ddflow configure` wrote -- so the documented
    way to set the test command could not set the test command. Splitting gates into
    their own file is still supported for operators who want it; it is just not the
    default, because a default that creates two sources of truth will produce two
    sources of truth.
    """
    return """# ddflow configuration — everything in one file.
# `ddflow config --explain` documents every knob. Only what you change needs to be
# here; everything else keeps its default.

# ---------------------------------------------------------------------------------
# THE ONE THING YOU MUST SET: how this project runs its tests.
# ---------------------------------------------------------------------------------
# [gate.unit_tests]
# command = "pytest -q -n auto"   # or "npm test" · "cargo test" · "go test ./..." · "make check"
#
# Set it with:   ddflow config --set gate.unit_tests.command "pytest -q -n auto"
# Run it in PARALLEL: `-n auto` needs pytest-xdist (`uv add --dev pytest-xdist`); without
# it, drop the flag. A serial run of a large suite is the slowest step of every item.
# Until it is set, the unit_tests gate reports UNAVAILABLE — which is honest, and
# blocks completion, rather than passing vacuously.
#
# Left COMMENTED on purpose: an empty table here would collide with the block that
# `ddflow config --append-toml` writes, since TOML forbids a duplicate table, and the
# documented way to configure the project would fail on a fresh install.

# ---------------------------------------------------------------------------------
# What is committed here vs what is local: this file is GENERIC project policy (the
# test command, pipelines, gates) that every clone must agree on. Your own endpoints,
# hosts, API-key variable names and machine sizing are git-ignored and go in
# .ddflow/local/ (`ddflow config --local --set ...`).
#
# A cross-family reviewer makes the `critic` gate real rather than self-reported.
# `ddflow reviewers detect --write` finds a local model server and writes it to
# .ddflow/local/reviewers.toml (`reviewers add` does the same; --shared commits it here
# instead). The block below is only the shape such an entry takes.
# ---------------------------------------------------------------------------------
# [[reviewer]]
# name     = "local"
# base_url = "http://127.0.0.1:11434/v1"
# model    = "qwen3:8b"
# family   = "alibaba"            # must differ from the authoring model's family
# gates    = ["critic"]
# api_key_env = "MY_API_KEY"      # the NAME of an env var, never the key itself

[lease]
ttl_s = 1800            # how long a claim survives without a heartbeat
heartbeat_s = 300

[worktree]
enabled = true
max_parallel = 4

[schedule]
max_parallel_tasks = 4

[enforce]
# "block" makes the pre-commit hook REFUSE a commit touching paths no lease of yours
# covers — the only layer of this workflow that does not rely on the agent agreeing.
# Starts at "warn" so adopting ddflow never breaks an existing repo on day one.
commit_without_lease = "warn"

[session]
brief_max_tokens = 1200
"""


#: The `.ddflow/.gitignore` ddflow owns outright. A constant, not an append: every line
#: in it is a claim about what ddflow itself writes into `.ddflow/`, so a stale copy
#: is wrong and the current one is the only right content.
DDFLOW_GITIGNORE = (
    "# The index and local state are DERIVED from events/ and are rebuildable.\n"
    "# They are machine-local on purpose: a committed index resurrects dead agents'\n"
    "# leases on every clone, and a committed cache is a merge conflict with no\n"
    "# meaningful resolution.\n"
    "index.db\nindex.db-*\nindex.rebuilding*\nevents.lock\n"
    "# What belongs to THIS machine -- your reviewer endpoints, API-key variable names,\n"
    "# test-worker counts -- goes in local/config.toml, local/gates.toml or\n"
    "# local/reviewers.toml, read last so it wins. reviewers.toml beside config.toml is\n"
    "# ignored as well: a LAN endpoint committed here reaches every clone.\n"
    "local/\n/reviewers.toml\n"
    "# The lock `config --set` / `workflow gate` take for a read-modify-write of a\n"
    "# config file. Without this line `git add .ddflow`, as `init` instructs, committed it.\n"
    ".*.lock\n"
    "# Every agent's task worktrees (decision D-worktree-home) and per-run logs: git\n"
    "# worktrees and scratch, never committed.\n"
    "/worktrees/\n/runs/\n"
)

#: The line that makes two clones' event-log shards concatenate on merge instead of
#: conflicting. Without it the first parallel merge of the log is a hand-resolved conflict.
UNION_MERGE_LINE = ".ddflow/events/*.jsonl merge=union\n"


def _append_once(path: Path, present: frozenset[str], text: str) -> bool:
    """Append `text` to a file the PROJECT owns unless one of its lines is in `present`.

    Appended, never rewritten: `.gitignore` and `.gitattributes` usually carry the
    project's own lines, and the newline guard keeps a file without a trailing newline
    from gluing our first line onto its last one.

    Whole LINES are compared, not substrings (bug B63d0028716): `.ddflow/events/*.jsonl
    -diff` contains "ddflow/events" and is not the union merge, and `.ddflow-worktrees-old/`
    contains ".ddflow-worktrees" and ignores nothing of ours. A substring test let either
    one stand in for the rule and the rule was never written.
    """
    prev = path.read_text("utf-8") if path.exists() else ""
    if any(" ".join(line.split()) in present for line in prev.splitlines()):
        return False
    path.write_text(prev + ("" if prev.endswith("\n") or not prev else "\n") + text, "utf-8")
    return True


#: The root `.gitignore` lines that already ignore an in-repo worktree root.
_WORKTREES_IGNORED = frozenset(
    {".ddflow-worktrees", ".ddflow-worktrees/", "/.ddflow-worktrees", "/.ddflow-worktrees/"}
)


def init_files(repo: Path) -> list[str]:
    """Create `.ddflow/` and the repository files that make its log safe to commit.

    ONE implementation for every surface. These writes used to live in the CLI's
    `cmd_init`, which `ddflow adopt` called and `ddflow_setup` over MCP never did (bug
    B185ec008b4): a project onboarded over MCP had a committable index, worktrees showing
    as untracked noise, and an event log that conflicted on the first parallel merge.
    `adopt` calls this, so both surfaces converge; `ddflow init` calls it alone.

    Idempotent. The starter config is written only when absent -- it is the project's
    once written -- and the root `.gitignore` / `.gitattributes` are appended to, never
    replaced. `.ddflow/.gitignore` is ddflow's own and is brought to the current text.
    """
    repo = Path(repo)
    actions: list[str] = []
    d = repo / ".ddflow"
    (d / "events").mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.is_file() or gi.read_text("utf-8") != DDFLOW_GITIGNORE:
        gi.write_text(DDFLOW_GITIGNORE, "utf-8")
        actions.append("wrote .ddflow/.gitignore")
    cfgp = d / "config.toml"
    if not cfgp.exists():
        cfgp.write_text(starter_config(), "utf-8")
        actions.append("wrote .ddflow/config.toml (starter)")
    # An in-repo worktree root (the default inside a container, where a sibling path
    # would land on the ephemeral layer) must be ignored, or every worktree shows up as
    # hundreds of untracked files and the enforcement hook trips over them.
    if _append_once(
        repo / ".gitignore",
        _WORKTREES_IGNORED,
        "\n# ddflow task worktrees (git worktrees; never commit them)\n.ddflow-worktrees/\n",
    ):
        actions.append("added .ddflow-worktrees/ to .gitignore")
    if _append_once(
        repo / ".gitattributes", frozenset({UNION_MERGE_LINE.strip()}), UNION_MERGE_LINE
    ):
        actions.append("added merge=union for .ddflow/events/*.jsonl to .gitattributes")
    # `[lease] append_only_globs` get their union line too (D-shared-globs): re-synced
    # here, so a config edited by hand is caught up by `init` / `adopt`.
    from . import shared_files as SF

    actions += [f"added '{ln}' to .gitattributes" for ln in SF.sync_attributes(repo)]
    return actions


def _driver_pairs(repo: Path, docs_dir: str, templates: Path) -> list[tuple[str, Path, Path]]:
    """(repo-relative path, the project's copy, the template) for each driver doc that EXISTS.

    `implement-phase.md` plus every delta already present that this ddflow ships a template
    for: the deltas on disk are the record of which agents the project was adopted for, so
    a refresh never adds one the operator did not adopt.
    """
    src = templates / "drivers"
    dst = Path(repo) / docs_dir / "drivers"
    pairs = [
        (
            f"{docs_dir}/drivers/implement-phase.md",
            dst / "implement-phase.md",
            src / "implement-phase.md",
        )
    ]
    present = sorted((dst / "deltas").glob("*.md")) if (dst / "deltas").is_dir() else []
    for path in present:
        tmpl = src / "deltas" / path.name
        if tmpl.is_file():
            pairs.append((f"{docs_dir}/drivers/deltas/{path.name}", path, tmpl))
    return pairs


def driver_drift(
    repo: Path, *, docs_dir: str = "docs/ddflow", package_dir: Path | None = None
) -> list[str]:
    """Driver docs whose bytes differ from the templates the RUNNING ddflow ships.

    `rules_status` judges the AGENTS.md block; nothing judged the driver docs themselves,
    so a project adopted by an older ddflow kept a driver that lacked later guidance while
    doctor said Healthy (bug Ba11a054309). Empty for a project that was never adopted.
    """
    if not has_been_adopted(repo, docs_dir=docs_dir):
        return []
    templates = Path(package_dir) / "templates" if package_dir else paths.templates_dir()
    if not (templates / "drivers" / "implement-phase.md").is_file():
        return []  # a packaging fault `adopt` names; doctor must not crash on it
    return [
        rel
        for rel, mine, tmpl in _driver_pairs(repo, docs_dir, templates)
        if mine.read_bytes() != tmpl.read_bytes()
    ]


def refresh_docs(
    repo: Path, *, docs_dir: str = "docs/ddflow", package_dir: Path | None = None
) -> list[str]:
    """Rewrite ONLY the agent-facing documents: the driver docs, the managed rules blocks
    and the adopted agents' native rules. Never the MCP launch, the hooks, the command
    files, `.gitignore`/`.gitattributes` or `.ddflow/` -- the parts of a plain `adopt` an
    operator may have tuned by hand and that a docs refresh has no business touching.

    Refuses (ValueError) a project that was never adopted: a refresh must not adopt.
    """
    repo = Path(repo)
    if not has_been_adopted(repo, docs_dir=docs_dir):
        raise ValueError(
            f"{repo} has not been adopted (no {docs_dir}/drivers/implement-phase.md); "
            "run `ddflow adopt` first"
        )
    templates = Path(package_dir) / "templates" if package_dir else paths.templates_dir()
    if not (templates / "drivers" / "implement-phase.md").is_file():
        raise FileNotFoundError(
            f"driver templates are missing from {templates}. This is a packaging fault, "
            f"not a configuration one: reinstall ddflow-mcp."
        )
    actions: list[str] = []
    agents = adopted_agents(repo, docs_dir=docs_dir)
    for rel, mine, tmpl in _driver_pairs(repo, docs_dir, templates):
        if mine.read_bytes() == tmpl.read_bytes():
            actions.append(f"{rel} is current")
        else:
            shutil.copy2(tmpl, mine)
            actions.append(f"wrote {rel}")
    section = project_section(docs_dir)
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = repo / name
        if name == "CLAUDE.md" and not path.exists() and "claude" not in agents:
            continue
        actions.append(_upsert_block(path, section))
    for key in agents:
        if key in NATIVE_RULES:
            actions.append(_write_native_rule(repo, key, docs_dir))
    return actions


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

    # Validate EVERY name before writing ANYTHING. The check used to sit inside the copy
    # loop, after the canonical driver was already written, so `--agents claude-code`
    # left a file behind and then exited 1 -- a partial adoption reported as a failure.
    unknown = [k for k in agents if k not in AGENT_TARGETS]
    if unknown:
        raise ValueError(
            f"unknown agent(s) {', '.join(map(repr, unknown))}; known: {', '.join(AGENT_TARGETS)}"
        )

    actions.extend(init_files(repo))

    drivers_dst = repo / docs_dir / "drivers"
    drivers_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(templates / "drivers" / "implement-phase.md", drivers_dst / "implement-phase.md")
    actions.append(f"wrote {docs_dir}/drivers/implement-phase.md")

    (drivers_dst / "deltas").mkdir(exist_ok=True)
    for key in agents:
        delta = AGENT_TARGETS[key].delta
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
        actions.extend(_install_prompt_hooks(repo, agents))
    for key in agents:
        actions.append(_register_mcp(repo, key, launch=launch, image=image))
        if key in NATIVE_RULES:
            actions.append(_write_native_rule(repo, key, docs_dir))
        for dst, src in AGENT_COMMANDS.get(key, {}).items():
            actions.append(_write_command(repo, dst, templates / src))
    from .enforce import redirect_note

    # Only when a line written here carries a path: a uvx or docker entry has none, and
    # the hooks' own install message already says it for the hooks.
    entry = _launch_entry(launch, image)
    line = (
        f'PYTHONPATH="{entry["env"]["PYTHONPATH"]}" {entry["command"]}' if entry.get("env") else ""
    )
    if line and (note := redirect_note(line)) and note not in "\n".join(actions):
        actions.append(note)
    return actions


def _install_prompt_hooks(repo: Path, agents: list[str]) -> list[str]:
    """The prompt-capture hook for each adopted agent that has one (Claude Code, Gemini CLI).

    A settings file ddflow cannot parse is reported, not overwritten, and never fails the
    adoption.
    """
    from . import claudehooks as CH
    from .enforce import command_line

    out: list[str] = []
    for key, rel, event, flag in (
        ("claude", ".claude/settings.json", CH.PROMPT_EVENT, ""),
        ("gemini", CH.GEMINI_SETTINGS, CH.GEMINI_PROMPT_EVENT, " --gemini"),
    ):
        if key not in agents:
            continue
        cmd = (
            command_line(
                CH.PROMPT_MARKER, extra=flag.strip(), refresh=f"ddflow hooks install --{key}"
            )
            + " || true"
        )
        try:
            out.append(
                CH.install(repo, cmd, event=event, marker=CH.PROMPT_MARKER, matcher=None, rel=rel)
            )
        except CH.SettingsError as exc:
            out.append(f"prompt hook not installed: {exc}")
    return out


#: Slash-command files an agent reads from the project, keyed like `AGENT_TARGETS`:
#: {repo-relative destination: source under `templates/`}. The MCP prompt of the same name
#: already reaches every client; this is for a harness whose loop primitive (Claude Code's
#: `/loop`) takes a slash command, so `/implement` can hand the workflow to it unattended.
AGENT_COMMANDS: dict[str, dict[str, str]] = {
    "claude": {".claude/commands/implement.md": "commands/claude/implement.md"},
}

#: The line that marks a command file as ddflow's own. A file without it is the operator's
#: -- a project adopting mid-stream often has an `/implement` of its own already, and
#: overwriting it would destroy the one workflow the project actually runs.
MANAGED_MARK = "<!-- DDFLOW:MANAGED"


def _write_command(repo: Path, rel: str, src: Path) -> str:
    path = repo / rel
    text = src.read_text("utf-8")
    if path.exists() and not path.is_file():
        return Refused(f"SKIPPED {rel}: it exists and is not a file; move it and re-run adopt")
    if path.exists():
        existing = path.read_text("utf-8")
        if existing == text:
            return f"{rel} is current"
        if MANAGED_MARK not in existing:
            return (
                f"kept {rel}: it is the project's own, not ddflow's. The ddflow version is "
                f"the MCP prompt `implement`. Delete the file and re-run adopt to take it"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return f"wrote {rel}"


def _write_native_rule(repo: Path, key: str, docs_dir: str = "docs/ddflow") -> str:
    """Write the agent's own rules file, for agents whose native surface outranks
    `AGENTS.md`.

    The body is the SAME managed block, so there is one source for the project text and
    the two copies cannot say different things. Only the frontmatter differs, and it is
    what makes the rule bind: `alwaysApply: true`, because claim-before-you-edit is not
    a rule that should depend on the model choosing to load it.
    """
    rule = NATIVE_RULES[key]
    path = repo / rule.path
    path.parent.mkdir(parents=True, exist_ok=True)
    if rule.form == FORM_WHOLE:
        # The whole file is ours: the frontmatter has to come FIRST for the rule to bind,
        # so there is nowhere to put a marker above it.
        path.write_text(native_rule_text(docs_dir), "utf-8")
        return f"wrote {rule.path} (always-applied project rule)"
    if rule.form == FORM_BLOCK:
        # A file the project may already own (`QWEN.md`, `replit.md`, `.goosehints`), so a
        # managed block rather than a wholesale write — the operator's own rules stay.
        return _upsert_block(path, project_section(docs_dir))
    if rule.form == FORM_AIDER:
        return _add_aider_read(path)
    raise ValueError(f"unknown native rules form {rule.form!r}")


#: The instruction file Aider must be told to load. One name, used by the writer and by
#: the checker, so "did we wire Aider up?" has one answer.
AIDER_READS = "AGENTS.md"


#: Does this text list `AGENTS.md` under `read:`, in EITHER YAML form?
#:
#: One pattern, used by the writer's idempotency guard and by `rules_status`. They used to
#: carry a line-anchored copy each while the writer could also emit an INLINE list, so
#: `adopt` produced `read: [CONVENTIONS.md, AGENTS.md]` -- correct YAML that both then
#: reported as not-binding. `doctor` failed a project that had just been adopted correctly,
#: and each re-adopt appended again, growing the file without bound. Found by roborev on
#: 3040d4b, reproduced end to end.
_AIDER_LISTED = re.compile(
    r"^\s*(?:-\s*)?AGENTS\.md\s*$"  # block form:  - AGENTS.md
    r"|^read:.*?\[[^\]]*\bAGENTS\.md\b[^\]]*\]",  # inline form: read: [x, AGENTS.md]
    re.M,
)


def _aider_lists_agents_md(text: str) -> bool:
    return bool(_AIDER_LISTED.search(text))


def _add_aider_read(path: Path) -> str:
    """Add `AGENTS.md` to `read:` in `.aider.conf.yml`.

    Aider discovers no instruction file at all -- not `AGENTS.md`, not a convention of its
    own -- so without this entry the rules are present in the repository and invisible to
    the agent. That makes this the one native surface where doing nothing is silent total
    failure rather than degraded behaviour.

    Edited as TEXT, not through a YAML round-trip: the file is the operator's, it may carry
    comments and ordering that matter to them, and a dump-and-rewrite would quietly discard
    both. `yaml` is also not a dependency of this package and should not become one to add
    a line.

    **Every form is normalised to the BLOCK form.** Writing an inline list back out was the
    source of three bugs at once: the reader did not recognise it, a trailing comment landed
    inside the brackets (`read: [CONVENTIONS.md]  # our docs, AGENTS.md]` -- unparseable,
    and the operator's own entry lost with it), and `read: []` became `read: [, AGENTS.md]`.
    One representation means the reader and the writer cannot disagree.
    """
    text = path.read_text("utf-8") if path.exists() else ""
    if _aider_lists_agents_md(text):
        return f"{path.name} already loads {AIDER_READS}"
    m = re.search(r"^read:([^\n]*)$", text, re.M)
    if m is None:
        prefix = text.rstrip() + "\n" if text.strip() else ""
        path.write_text(f"{prefix}read:\n  - {AIDER_READS}\n", "utf-8")
        return f"{'added' if prefix else 'created'} read: {AIDER_READS} in {path.name}"

    existing, trailing = _aider_read_values(m.group(1))
    # Any following block-form entries belong to this key too, and must survive.
    rest = text[m.end() :]
    # Past the newline that ENDS the `read:` line, or the first iteration below sees "\n"
    # and stops before reading a single block entry -- which left the operator's entries in
    # the file but moved below ours, a reordering with no reason behind it.
    lead = len(rest) - len(rest.lstrip("\n"))
    rest = rest[lead:]
    consumed = 0
    for line in rest.splitlines(keepends=True):
        item = re.match(r"^\s+-\s*(.+?)\s*$", line)
        if not item:
            break
        existing.append(item.group(1))
        consumed += len(line)

    values = [*dict.fromkeys([*existing, AIDER_READS])]  # de-duplicated, order kept
    block = "read:" + trailing + "\n" + "".join(f"  - {v}\n" for v in values)
    path.write_text(text[: m.start()] + block + rest[consumed:], "utf-8")
    return f"added {AIDER_READS} to read: in {path.name}"


def _aider_read_values(after_colon: str) -> tuple[list[str], str]:
    """`(values, trailing-comment)` for whatever followed `read:` on its own line.

    Handles the three forms an operator may have written -- an inline list, a bare scalar,
    or nothing (a block list follows) -- and keeps any trailing comment OUTSIDE the values,
    which is the bug that made `read: [x]  # note` unparseable when the comment was treated
    as part of the list.
    """
    raw = after_colon.strip()
    comment = ""
    bracket = re.match(r"^\[([^\]]*)\](.*)$", raw)
    if bracket:
        inner, comment = bracket.group(1), bracket.group(2)
        values = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
        return values, (" " + comment.strip() if comment.strip() else "")
    if raw.startswith("#"):
        return [], " " + raw
    if "#" in raw:
        raw, comment = raw.split("#", 1)
        comment = " #" + comment
    raw = raw.strip()
    return ([raw] if raw else []), comment


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
    """The directory containing the `ddflow` package that launch lines point at — what
    goes on PYTHONPATH. The primary checkout when run from a linked worktree of ddflow
    (`infra.paths.launch_parent`)."""
    from ..infra.paths import launch_parent

    return str(launch_parent())


def _python() -> str:
    from ..infra.paths import launch_python

    return launch_python()


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

    if launch == "auto" and not _running_from_source() and not _installed_from_index():
        # Installed, but not from an index: from git, a local path or an archive URL
        # (bug B8ff258154d). `uvx ddflow-mcp` resolves the name against PyPI, which is
        # a different version at best and a 404 before the first release, so the entry
        # must name THIS installation.
        return _installed_entry()
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
            "command": _python(),
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
        "command": _python(),
        "args": ["-m", MCP_MODULE],
        "env": {"PYTHONPATH": pkg_parent},
    }


def _running_from_source() -> bool:
    """True when this module lives in a checkout rather than in site-packages."""
    return _INSTALL.running_from_source()


#: The distribution name on the index, and what `uvx` resolves.
DIST_NAME = _INSTALL.DIST_NAME


def _own_distribution():
    """The installed distribution that THIS `ddflow` package came from, or None."""
    return _INSTALL.own_distribution()


def _installed_from_index() -> bool:
    """True when this installation came from a package index, so `uvx ddflow-mcp`
    reaches the same project (PEP 610; see `install_info`)."""
    return _INSTALL.installed_from_index()


def _installed_entry() -> dict[str, object]:
    """Launch THIS installation: the `ddflow-mcp` script installed beside the running
    interpreter when there is one (what `uv tool install` / `pipx install` put in the
    tool's environment), else that interpreter with the package's directory on
    PYTHONPATH. Absolute paths both: an agent's environment need not share this PATH."""
    exe = Path(_python())
    for name in (DIST_NAME, DIST_NAME + ".exe"):
        script = exe.parent / name
        if script.is_file():
            return {"command": str(script), "args": []}
    return {
        "command": str(exe),
        "args": ["-m", MCP_MODULE],
        "env": {"PYTHONPATH": _package_parent()},
    }


class Refused(str):
    """An action message that is a REFUSAL: nothing was written, and the operator must
    act. A `str`, so `adopt`'s action list reads exactly as before; a distinct TYPE, so
    `setup` can fail on it without sniffing the wording. It used to be a plain string,
    and `adopt` printed "SKIPPED ..." then "ddflow adopted for: kilo" and exited 0 --
    a server never registered, reported as done (roborev on 7216f5e). The same contract
    `companions add` already keeps (`test_a_refusal_is_not_reported_as_success`)."""


def _register_mcp(
    repo: Path, key: str, *, launch: str = "auto", image: str = "ghcr.io/OWNER/ddflow:latest"
) -> str:
    """Add the ddflow MCP server to one agent's config, preserving what is there.

    Merged rather than overwritten: these files hold the user's other servers, and a
    tool that stomps them is a tool nobody runs twice.
    """
    target = AGENT_TARGETS[key]
    if not target.config or target.shape == SHAPE_NONE:
        return (
            f"{key}: no project-level MCP config file exists — register the server in its "
            f"own settings (see {_DOCS_HINT})"
        )
    rel = target.config
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    # `uvx` fetches and runs the published package in an ephemeral environment, so a
    # project adopting ddflow needs no clone, no virtualenv, no PYTHONPATH and no
    # install step an operator can forget. When ddflow is running from a source
    # checkout rather than an installed distribution, fall back to that checkout --
    # otherwise developing ddflow would silently configure the project against the
    # PUBLISHED version instead of the one under test.
    entry = _launch_entry(launch, image)

    if target.shape == SHAPE_TOML:
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
            return Refused(f"SKIPPED {rel}: it is not valid JSON; add the server by hand")
    try:
        place_server(data, target.shape, "ddflow", entry)
    except UnplaceableConfig as exc:
        return Refused(f"SKIPPED {rel}: {exc}; add the server by hand")
    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    return f"registered ddflow in {rel}"


def server_entry_for(shape: str, entry: dict) -> dict:
    """``entry`` rewritten the way THIS agent's config must store it.

    Only opencode (and Kilo, its fork) differs, and it differs in a way that fails silently: `command` is one
    ARRAY including the arguments, the transport is named rather than inferred, and
    `enabled` is explicit. Handing it the common `{"command": str, "args": [...]}` form
    produces valid JSON that starts nothing.
    """
    if shape == SHAPE_OPENCODE:
        out: dict = {
            "type": "local",
            "command": [entry["command"], *entry.get("args", [])],
            "enabled": True,
        }
        if entry.get("env"):
            out["environment"] = entry["env"]
        return out
    if shape == SHAPE_SERVERS:
        # VS Code names the transport in the entry; its own documented example carries
        # `"type": "stdio"`, so that is what is written rather than relying on it being
        # inferred from the presence of `command`.
        return {"type": "stdio", **entry}
    if shape == SHAPE_COPILOT:
        # Copilot CLI calls a stdio server "local", and `tools` is its allowlist -- absent,
        # a server's tools are not offered. `["*"]` means "all of ddflow's tools", which is
        # the only useful setting for a queue the agent is supposed to drive.
        return {"type": "local", **entry, "tools": ["*"]}
    return dict(entry)


def _server_container(data: dict, shape: str, *, create: bool) -> dict | None:
    """The dict inside ``data`` that maps server NAME -> entry, for this shape.

    One place that knows where servers live in each file. There used to be three -- a
    ternary in the adopter, `_json_field` in the companions writer and a
    `SERVERS_FIELD_AGENTS` set beside it -- so adding an agent meant editing three
    things that no test tied together, and only one of them could express nesting.
    """
    if shape == SHAPE_OPENCODE:
        path: tuple[str, ...] = ("mcp",)
    elif shape == SHAPE_MCP_DOT_SERVERS:
        path = ("mcp", "servers")
    elif shape in (SHAPE_MCP_SERVERS, SHAPE_COPILOT):
        path = ("mcpServers",)
    elif shape == SHAPE_SERVERS:
        path = ("servers",)
    else:
        raise ValueError(f"unknown MCP config shape {shape!r}")
    node = data
    for part in path:
        if create:
            node = node.setdefault(part, {}) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                return None
        else:
            node = (node.get(part) if isinstance(node, dict) else None) or {}
            if not isinstance(node, dict):
                return None
    return node


class UnplaceableConfig(ValueError):
    """The operator's file is valid JSON but holds something other than an object where
    servers live -- `{"mcp": null}`, `{"mcp": ["x"]}`, a top-level list. Replacing it
    would destroy their data; guessing a merge would be worse. The caller says SKIPPED."""


def place_server(data: dict, shape: str, name: str, entry: dict) -> None:
    """Put one server into ``data`` where ``shape`` says it belongs.

    Mutates in place and preserves every sibling: these files hold the operator's other
    servers, and a tool that stomps them is a tool nobody runs twice. Raises
    `UnplaceableConfig` rather than crash (it used to be an `assert`, and a TypeError for
    a list) when the file holds a non-object where servers go.
    """
    container = _server_container(data, shape, create=True)
    if container is None:
        raise UnplaceableConfig(f"not a JSON object where {shape!r} servers belong")
    container[name] = server_entry_for(shape, entry)


def get_server(data: dict, shape: str, name: str) -> Any:
    """What ``data`` currently stores for ``name``, or None. The read half of `place_server`."""
    container = _server_container(data, shape, create=False)
    return (container or {}).get(name)


def get_servers(data: dict, shape: str) -> dict:
    """Every server ``data`` stores, by name: the container `get_server` reads, whole."""
    return dict(_server_container(data, shape, create=False) or {})
