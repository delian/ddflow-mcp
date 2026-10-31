"""The managed rules block and what `rules_status` judges.

`AGENTS.md` is the one instructions file more than thirty agents read; this is where its
block is generated, stamped and compared, and where a project's rules surfaces are checked
against it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ...infra.fsio import Managed, RegionError
from ._aider import AIDER_READS, _aider_lists_agents_md
from ._targets import AGENT_TARGETS, FORM_AIDER, FORM_BLOCK, NATIVE_RULES

#: The managed rules block in `AGENTS.md`, `CLAUDE.md` and the block-form native rules: the
#: D-doc-regions grammar with the writer's version, format level and body digest
#: (`fsio.Managed`), so an older, newer or hand-edited block is told apart, not just "differs".
BLOCK = Managed("rules/work-queue")

#: The markers ddflow wrote before the stamped grammar. Still READ -- a project adopted by an
#: older ddflow carries them -- and migrated to `BLOCK` the next time the block is written.
LEGACY_BEGIN = "<!-- DDFLOW:BEGIN (managed — edits inside this block are overwritten) -->"
LEGACY_END = "<!-- DDFLOW:END -->"

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
_SECTION = """## Work queue — ddflow

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

Full driver: [`{driver}`]({driver}) · per-agent notes: `{deltas}/`
"""

_DRIVER_LINE = "Full driver: [`{driver}`]({driver}) · per-agent notes: `{deltas}/`"


def project_body(docs_dir: str = "docs/ddflow") -> str:
    """The text of the managed block this version of ddflow would write, without its markers.

    Factored out of `adopt` so `rules_status` can compare what is ON DISK against what
    SHOULD be there. Two generators would be two answers to the same question, and the
    drift between them would be invisible in exactly the file that tells an agent how to
    behave.
    """
    return _SECTION.format(
        driver_line=_DRIVER_LINE.format(
            driver=f"{docs_dir}/drivers/implement-phase.md",
            deltas=f"{docs_dir}/drivers/deltas",
        ),
    )


def project_section(docs_dir: str = "docs/ddflow") -> str:
    """The managed block as written to a file: `project_body` between its stamped markers."""
    return BLOCK.render(project_body(docs_dir))


def block_body(text: str) -> str | None:
    """The managed block's body in ``text`` -- under the stamped markers or the legacy ones --
    or None when the file holds no block. Raises RegionError for broken markers."""
    at = BLOCK._region().find(text)
    if at is not None:
        return text[at[1] : at[2]]
    if has_legacy_block(text):
        return text[text.index(LEGACY_BEGIN) + len(LEGACY_BEGIN) : text.index(LEGACY_END)]
    if LEGACY_BEGIN in text and LEGACY_END in text:
        raise RegionError("the legacy block markers are reversed")
    return None


def has_legacy_block(text: str) -> bool:
    """Is there a block under the legacy markers (begin before end, no stamped block too)?"""
    return (
        LEGACY_BEGIN in text
        and LEGACY_END in text
        and text.index(LEGACY_BEGIN) + len(LEGACY_BEGIN) <= text.index(LEGACY_END)
        and not BLOCK.owns(text)
    )


def block_marker(line: str) -> str:
    """`"begin"` or `"end"` when this line is the managed block's marker (either grammar),
    else ""."""
    if LEGACY_BEGIN in line or f"ddflow:begin {BLOCK.name}" in line:
        return "begin"
    if LEGACY_END in line or f"ddflow:end {BLOCK.name}" in line:
        return "end"
    return ""


def block_spans(text: str) -> list[tuple[int, int]]:
    """Where each managed block sits in ``text``, in file order: `(start, stop)` offsets from
    the begin line through the end line (newline included), whichever marker grammar it is
    written in. A marker is a whole line starting in column 0, so one quoted in prose or
    shown indented as an example is not one. Raises
    RegionError for markers that do not pair up (a begin inside a block, an end outside)."""
    region = BLOCK._region()
    begin, end = re.compile(region.begin or ""), re.compile(region.end or "")
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    at = 0
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\n").removesuffix("\r")
        if begin.fullmatch(bare) or bare == LEGACY_BEGIN:
            if open_at is not None:
                raise RegionError("a managed block begins inside another")
            open_at = at
        elif end.fullmatch(bare) or bare == LEGACY_END:
            if open_at is None:
                raise RegionError("a managed block ends without a beginning")
            spans.append((open_at, at + len(line)))
            open_at = None
        at += len(line)
    if open_at is not None:
        raise RegionError("a managed block begins and never ends")
    return spans


def block_count(text: str) -> int:
    """How many managed blocks ``text`` holds (both marker grammars); 0 for a file whose
    markers do not pair up (`_block_state` reports that as a removed section)."""
    try:
        return len(block_spans(text))
    except RegionError:
        return 0


#: The rules surfaces whose state `rules_status` reports. `AGENTS.md` is the one more than
#: thirty agents read; `CLAUDE.md` only counts once it exists, because writing it into a
#: project that does not use Claude Code would be noise.
RULES_FILES = ("AGENTS.md", "CLAUDE.md")

MISSING, NO_BLOCK, STALE, CURRENT = "missing", "no_block", "stale", "current"
#: More than one managed block in one file (an older ddflow appended its own beside a newer
#: one's): which to trust is ambiguous, so it is a problem, not drift. A refresh collapses them.
DUPLICATE = "duplicate"
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
    #: The stamped block's body no longer matches the digest it was written with: a person
    #: changed it (so a refresh keeps their version in `.local-edits`).
    edited: bool = False
    #: The block carries the stamped markers (so "differs" is proven to be an unedited older
    #: copy unless `edited`); False for the legacy markers, which hold no digest.
    stamped: bool = False
    #: The stamped block was written at a higher format level than this ddflow understands.
    newer: bool = False
    #: How many managed blocks the file holds (`block_count`); more than one is `DUPLICATE`.
    blocks: int = 0

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
            STALE: self._stale_line(),
            DUPLICATE: (
                f"{self.path} holds {self.blocks} managed blocks, where one is meant "
                "(an older ddflow's adopt appended its own beside the newer one's)"
            ),
            CURRENT: f"{self.path} is current",
        }[self.state]

    def _stale_line(self) -> str:
        """Say WHICH difference this is, from what the stamp proves (D-compat 2): a section a
        newer ddflow wrote is not 'older', a hand edit is not 'from an older version', and a
        copy with no stamp cannot be told either way."""
        if self.newer:
            return (
                f"{self.path}'s ddflow section was written at a newer format level than "
                "this ddflow understands; upgrade ddflow (a refresh will not overwrite it)"
            )
        if self.edited:
            return (
                f"{self.path}'s ddflow section was edited by hand since ddflow wrote it "
                "(a refresh keeps your version in a .local-edits file)"
            )
        if self.stamped:
            return f"{self.path}'s ddflow section is from an older version and has drifted"
        return (
            f"{self.path}'s ddflow section differs from what this ddflow writes "
            "(it carries no version stamp, so nothing says whether it is an older copy "
            "or was edited)"
        )

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
    return _NATIVE_FRONTMATTER + project_body(docs_dir).strip() + "\n"


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


def _stamped(text: str) -> bool:
    try:
        return BLOCK.stamp(text) is not None
    except RegionError:
        return False


def block_newer(text: str) -> bool:
    """Was the stamped managed block in ``text`` written at a newer format level?"""
    try:
        return BLOCK.state(text) == "newer"
    except RegionError:
        return False


def block_version(path: Path) -> str | None:
    """The version stamped on the managed block in the file at ``path``; None when the file
    is unreadable, holds no stamped block, or holds several stamped ones (which one wrote it
    is moot); a legacy block beside the one stamped block does not matter."""
    try:
        text = Path(path).read_text("utf-8", errors="replace")
        return BLOCK.version(text)
    except (OSError, RegionError):
        return None


def block_edited(text: str) -> bool:
    """Was the stamped managed block in ``text`` changed by hand since it was written?"""
    try:
        return BLOCK.state(text) == "edited"
    except RegionError:
        return False


def _block_state(text: str, want: str) -> str:
    """CURRENT when the file's managed block says what this ddflow would write, NO_BLOCK when
    it holds none (or its markers are broken), else STALE. A block under the legacy markers
    is STALE even when its words match: the next write migrates it to the stamped grammar.
    More than one block, in either grammar, is DUPLICATE."""
    if block_count(text) > 1:
        return DUPLICATE
    try:
        body = block_body(text)
    except RegionError:
        return NO_BLOCK
    if body is None:
        return NO_BLOCK
    if has_legacy_block(text) or body.strip() != want:
        return STALE
    return CURRENT


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
    want = project_body(docs_dir).strip()
    for name in RULES_FILES:
        path = Path(repo) / name
        if not path.exists():
            if name != "AGENTS.md":
                continue  # not a defect — see the docstring
            out.append(RulesState(name, MISSING))
            continue
        text = path.read_text("utf-8", errors="replace")
        out.append(
            RulesState(
                name,
                _block_state(text, want),
                block_edited(text),
                _stamped(text),
                block_newer(text),
                block_count(text),
            )
        )

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
    want_block = want
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
            out.append(
                RulesState(
                    rule.path,
                    _block_state(got, want_block),
                    block_edited(got),
                    _stamped(got),
                    block_newer(got),
                    block_count(got),
                )
            )
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
