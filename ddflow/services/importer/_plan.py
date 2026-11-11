"""`plan_import` and `verify_import`."""

from __future__ import annotations

import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ...config import Config
from ...infra.log import EventLog
from ._phases import (
    _claims_done,
    _note_withheld,
    _scan_sources,
    _triage_found,
    touched_since_import,
)
from ._scan import (
    _NOTE_EXAMPLES,
    _flag_shipped_phases_with_open_tasks,
    _flag_unresolvable_needs,
    _known_ids,
    _pull_in_needed,
    scan_branches,
)
from ._tables import LEGACY_MEMORY_SESSION, NOTE_SESSIONS, Found, ImportPlan, _unique


def _cap_or_settle(
    repo: Path,
    plan: ImportPlan,
    state,
    known: set[str],
    max_tasks: int,
    include_done: bool,
    events: list | None,
    deferred_done: dict[str, Found],
    existing_phases: dict[str, Found],
) -> None:
    """Refuse an over-cap import, or drop empty phases and settle the finished ones."""
    tasks = [f for f in plan.found if f.kind == "task"]
    if len(tasks) > max_tasks:
        plan.found = [f for f in plan.found if f.kind not in ("task", "phase")]
        plan.notes.append(
            f"REFUSING to propose {len(tasks)} tasks (cap {max_tasks}). That many is "
            f"almost certainly a whole project history rather than a queue, and an "
            f"import writes events into a log that is committed. Narrow it — point the "
            f"import at one todo file, or raise the cap deliberately once you have "
            f"looked at what it would write. The phases are withheld with them: a "
            f"queue of empty phases is not a smaller import, it is a misleading one."
        )
    else:
        # A phase whose tasks were all filtered out -- finished, or already in the
        # queue -- is an empty container. Importing 790 of them alongside zero tasks
        # reads as "this project has 790 phases of work", which is the opposite of
        # true. Dropped here rather than at apply time so the PROPOSAL is what gets
        # written, and the operator reviews the thing that will happen.
        _flag_shipped_phases_with_open_tasks(plan)
        _flag_unresolvable_needs(plan, known)
        live_phases = {f.extra.get("phase") for f in plan.found if f.kind == "task"}
        # ...and any phase an open item declares a dependency ON. A finished phase that
        # open work waits for is not an empty container: drop it and the dependency
        # becomes unknown, which is treated as unmet, so the open item imports
        # permanently blocked while the import reports success. Same rule as the
        # finished-TASK carve-out above, and it was missing here.
        live_phases |= {d for f in plan.found for d in f.needs}
        empty_phases = [f for f in plan.found if f.kind == "phase" and f.ident not in live_phases]
        if empty_phases:
            plan.found = [f for f in plan.found if f not in empty_phases]
            plan.notes.append(
                f"{len(empty_phases)} phase(s) had no open task left and were not "
                f"imported. They are finished or already in the queue; an empty phase "
                f"is a container, not work."
            )
        if events is None and state is not None:
            # The same `[log]` config every other reader honours (the parse cache).
            events = EventLog(repo, log_cfg=Config.load(repo).log).read_all()
        touched = touched_since_import(events or ())
        if include_done:
            # AFTER the tasks are in the plan: a phase is finished or not by what is
            # under it once this import has run, not by what was under it before.
            # Read through the package's lazy re-export at call time, so a monkeypatch on
            # the package (`IM._settle_phases`) still reaches the caller (the split kept
            # the one-file invariant that patching the package rebinds the name).
            sys.modules[__package__]._settle_phases(plan, state, touched, existing_phases)
        else:
            sys.modules[__package__]._settle_needed_phases(
                plan, deferred_done, state, touched, existing_phases
            )


def plan_import(
    repo: Path,
    state=None,
    *,
    include_done: bool = False,
    max_tasks: int = 200,
    sources: dict[str, tuple[str, ...]] | None = None,
    archive: tuple[str, ...] = (),
    events: list | None = None,
) -> ImportPlan:
    """Read everything importable. Writes NOTHING.

    ``state`` is the already-folded queue, if there is one: anything whose id is
    already present is reported as skipped rather than proposed again, which is what
    makes a second run safe after the operator edits the todo file.

    **Completed tasks are excluded by default.** Run against the repository this was
    extracted from, the scan finds 4,799 ticked boxes — a faithful history and useless
    as a queue, because none of it is work anyone will do. What matters for "continue
    from where we left off" is the OPEN items, the branches still in flight, and the
    memory (lessons, decisions). `include_done=True` imports the rest as closed items
    when the history itself is what you want. Open items the source itself DISPOSES of
    (declined, refuted, struck through) are history too and follow the same rule; items
    it DEFERS import as blocked.

    ``max_tasks`` is a guard rail rather than a policy: an import that silently writes
    five thousand events into a log that is committed to git is not recoverable by
    anything short of editing history. Over the cap, the plan reports the overflow and
    refuses to propose it — narrow the scope, or raise the cap deliberately.

    ``sources`` maps a family (`todo`, `lesson`, ...) to the globs to read for it; a
    family absent from it reads its defaults. `sources_from(cfg)` builds it from config.
    ``archive`` names todo files whose open items are history until released: they import
    as BLOCKED (`[importer] archive_globs`).

    With ``include_done`` a phase whose every task is done or closed is proposed DONE --
    and an imported phase already in the queue gets a `completion` once the re-run has
    filled it in (`_settle_phases`). ``events`` is the log ``state`` was folded from, which
     says whether somebody touched such a phase since; read from ``repo`` when omitted.
    """
    # deferred: defined in _apply; read through the package's lazy re-export at call time
    _dedupe_found = sys.modules[__package__]._dedupe_found
    _leave_out_long_memories = sys.modules[__package__]._leave_out_long_memories

    plan = ImportPlan()
    known = _known_ids(state)
    sources = sources or {}

    proposed: set[str] = set()
    scanned = _scan_sources(repo, plan, sources, archive)
    closed, held, deferred_done, existing_phases = _triage_found(
        plan, scanned, known, proposed, include_done
    )

    # A finished task that an OPEN task depends on has to come too, as done. Skipping
    # it leaves the open one blocked on an id the queue has never heard of — and an
    # unknown dependency is treated as unmet, deliberately, so the import would land
    # permanently stuck work and look like it had succeeded.
    _pull_in_needed(plan, deferred_done, held)
    in_plan = {id(f) for f in plan.found}
    ticked = [f for f in deferred_done.values() if f.done and id(f) not in in_plan]
    plan.ticked_left_out = len(ticked)
    _note_withheld(plan, ticked, [f.ident for f in closed if id(f) not in in_plan], held)

    _cap_or_settle(
        repo, plan, state, known, max_tasks, include_done, events, deferred_done, existing_phases
    )
    _leave_out_long_memories(repo, plan)
    _dedupe_found(repo, state, plan)
    for f in scan_branches(repo):
        f.ident = _unique("", f.ident, proposed)
        if f.ident not in known:
            plan.found.append(f)

    refused = any(n.startswith("REFUSING") for n in plan.notes)
    if not plan.found and not plan.skipped_existing and not plan.duplicates and not refused:
        plan.notes.append(
            "Nothing recognisable was found. That is not necessarily wrong — this looks "
            "for todo checklists, a lessons corpus, ADR files and unmerged branches in "
            "their usual locations. If this project keeps them somewhere else, tell the "
            "agent where and it can file them with `ddflow task add` / `lesson add` / "
            "`decision add` directly."
        )
    return plan


@dataclass
class VerifyReport:
    """What an import left behind, and whether it is still true.

    Three questions, and they cost different amounts, which is why they are separate
    fields rather than one verdict:

    * **status** -- what is imported, per kind. Read from the folded queue; free.
    * **still true?** -- has the source moved since. Needs a fresh scan of every source
      file (~0.65 s on a 4,799-checkbox corpus), so it is done here and NOT at the MCP
      handshake.
    * **finished?** -- the judgement half the `import-existing-project` prompt asks a
      human for, which nothing checked until this existed. An imported queue nobody
      finished misrepresents the project exactly as an empty one does, and is believed
      harder because a tool produced it.

    Deliberately does NOT re-report what `ddflow doctor` already covers -- unresolved
    dependencies, duplicate globs, cycles. Two commands reporting one defect in
    different words is how an operator learns to read neither.
    """

    #: kind -> how many items of it carry import provenance.
    imported: dict[str, int] = field(default_factory=dict)
    #: Earliest and latest `created_at` among imported items; "" when none.
    first_at: str = ""
    last_at: str = ""
    #: What a re-run would add now, because the source files moved on.
    drift: list[Found] = field(default_factory=list)
    #: Matched a source pattern and yielded nothing -- usually an unusual format
    #: rather than an empty file, which is why it is reported and not ignored.
    empty_sources: list[str] = field(default_factory=list)
    #: `(item id, source)` for EVERY item whose file is gone -- not one per file. Built
    #: per-file and reported per-item, four items sharing `docs/todo.md` came out as one
    #: entry, and an agent auditing provenance fixes the one it was told about and
    #: believes it has finished.
    vanished: list[tuple[str, str]] = field(default_factory=list)
    #: Imported tasks with no globs. The conflict detector cannot protect them, so two
    #: agents can be handed the same file and neither is refused.
    no_globs: list[str] = field(default_factory=list)
    #: Imported BRANCHES with no globs, kept apart from `no_globs`. A branch arrives
    #: without them by construction -- git knows which commits it carries, not which
    #: files the work will touch -- so counting it as a task that failed to declare
    #: made the arithmetic lie ("2 task(s) declare no globs" under "1 branch, 2 tasks")
    #: and made the handshake say "never finished" forever about any repo with an
    #: unmerged branch.
    no_globs_branches: list[str] = field(default_factory=list)
    #: Phases whose title still claims the work shipped while a task under them is open.
    shipped_drift: list[str] = field(default_factory=list)
    #: Items carrying the prose "Imported from ..." body but no `source` FIELD --
    #: written by a version before the field existed. Reported as unknown rather than
    #: guessed at by regexing the sentence.
    unstructured: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.imported.values())

    @property
    def findings(self) -> list[str]:
        """One line per thing a human has to decide or fix. Empty means consistent."""
        out: list[str] = []
        if self.no_globs:
            out.append(
                f"{len(self.no_globs)} imported task(s) declare no globs, so the "
                f"conflict detector cannot protect them and two agents can be handed "
                f"the same file: {', '.join(self.no_globs[:_NOTE_EXAMPLES])}"
                + (", ..." if len(self.no_globs) > _NOTE_EXAMPLES else "")
            )
        if self.shipped_drift:
            out.append(
                f"{len(self.shipped_drift)} phase(s) say the work is finished while a "
                f"task under them is still open: "
                f"{', '.join(self.shipped_drift[:_NOTE_EXAMPLES])}"
                + (", ..." if len(self.shipped_drift) > _NOTE_EXAMPLES else "")
                + ". Ask the operator which is stale before anyone claims from them."
            )
        if self.drift:
            out.append(
                f"the source has moved on: {len(self.drift)} item(s) exist in the files "
                f"that are not in the queue. Re-run the import to pick them up."
            )
        if self.vanished:
            files = sorted({src for _i, src in self.vanished})
            out.append(
                f"{len(self.vanished)} imported item(s) across {len(files)} file(s) "
                f"name a source that no longer exists, so their provenance cannot be "
                f"checked: "
                + ", ".join(f"{i} ({src})" for i, src in self.vanished[:_NOTE_EXAMPLES])
                + (", ..." if len(self.vanished) > _NOTE_EXAMPLES else "")
            )
        if self.empty_sources:
            out.append(
                f"{len(self.empty_sources)} file(s) matched a source pattern and "
                f"yielded nothing, which usually means an unusual format rather than an "
                f"empty file: {', '.join(self.empty_sources[:_NOTE_EXAMPLES])}"
            )
        if self.no_globs_branches:
            out.append(
                f"{len(self.no_globs_branches)} imported branch(es) declare no globs. A "
                f"branch arrives without them -- git knows its commits, not which files "
                f"the work will touch -- so somebody has to say before it is claimed: "
                f"{', '.join(self.no_globs_branches[:_NOTE_EXAMPLES])}"
            )
        return out


def _scan_queue(state, r: VerifyReport) -> tuple[list[str], dict[str, list[str]]]:
    """Everything answerable from the folded queue. Touches no file.

    Returns `(created_at values, source path -> item ids)` for the caller's rescan.
    """
    ats: list[str] = []
    paths: dict[str, list[str]] = {}
    for it in getattr(state, "items", {}).values():
        if it.removed:
            continue
        if not it.source:
            # The prose body is the only remaining signal for an item imported before
            # provenance was a field. COUNTED, never parsed: a body is a sentence
            # someone may reword, and a count that silently becomes zero when they do
            # is worse than an admitted unknown.
            if it.body.startswith("Imported from "):
                r.unstructured.append(it.id)
            continue
        r.imported["branch" if it.source.startswith("git:") else it.kind] = (
            r.imported.get("branch" if it.source.startswith("git:") else it.kind, 0) + 1
        )
        if it.created_at:
            ats.append(it.created_at)
        if it.kind == "task" and not it.globs and not it.terminal:
            (r.no_globs_branches if it.source.startswith("git:") else r.no_globs).append(it.id)
        elif it.kind == "phase" and _claims_done(it.title) and _has_open_child(state, it):
            r.shipped_drift.append(it.id)
        if not it.source.startswith("git:"):
            paths.setdefault(it.source.split(":", 1)[0], []).append(it.id)

    for kind, holder in (
        ("lesson", getattr(state, "lessons", {})),
        ("decision", getattr(state, "decisions", {})),
        ("research", getattr(state, "research", {})),
    ):
        n = sum(1 for rec in holder.values() if "imported" in getattr(rec, "tags", []))
        if n:
            r.imported[kind] = n
    _note_sources(state, r, paths)
    imported_memories = [m for m in getattr(state, "memories", {}).values() if m.source]
    legacy = getattr(state, "sessions", {}).get(LEGACY_MEMORY_SESSION)
    legacy_notes = [n for n in (legacy.notes if legacy else []) if n.get("source")]
    if imported_memories or legacy_notes:
        r.imported["memory"] = len(imported_memories) + len(legacy_notes)
    for m in imported_memories:
        rel = m.source.split(":", 1)[0]
        if rel:
            paths.setdefault(rel, []).append(m.id)
    prose = _memory_sources(state, paths)
    if prose:
        # A NOTE: nobody looked at these, and a clean report must not read as if they
        # were checked and found present.
        r.notes.append(
            f"{prose} imported source(s) are prose that mentions a path rather than a "
            f"path, so they were not checked for a vanished file."
        )
    return ats, paths


def _note_sources(state, r: VerifyReport, paths: dict[str, list[str]]) -> None:
    """Imported notes, counted and their sources collected.

    Filtered on `source`, exactly as the memory records are filtered on the `imported`
    tag. `ddflow session note <sid>` takes ANY session id, so the import's own sinks
    are not private to it -- one hand-written note in `s-imported-journal` counted as
    an imported record, which is the missing-provenance-filter bug in the function
    written to stop guessing at provenance.
    """
    for kind, sid, _what in NOTE_SESSIONS:
        sess = getattr(state, "sessions", {}).get(sid)
        if not sess:
            continue
        imported = [n for n in sess.notes if n.get("source")]
        if imported:
            r.imported[kind] = len(imported)
        for n in imported:
            rel = str(n["source"]).split(":", 1)[0]
            if rel and not rel.startswith("git:"):
                paths.setdefault(rel, []).append(f"{sid}#{n.get('ident', n.get('seq', '?'))}")


def _memory_sources(state, paths: dict[str, list[str]]) -> int:
    """Source files named by imported lessons and research notes; returns how many
    sources mentioned a path but were prose, so were not checked.

    All three carry a STRUCTURED source -- `Lesson.seen_in`, `ResearchNote.sources`,
    `Decision.sources` -- so there is no reason for the vanished-source check to cover
    items only. Decisions were the gap until `Decision.sources` existed: theirs lived
    in the prose `context`, and parsing a path back out of a sentence is the
    anti-pattern this whole check exists to replace.
    """
    prose = 0
    for holder, attr in (
        (getattr(state, "lessons", {}), "seen_in"),
        (getattr(state, "research", {}), "sources"),
        (getattr(state, "decisions", {}), "sources"),
    ):
        for rec in holder.values():
            if "imported" not in getattr(rec, "tags", []):
                continue
            for src in getattr(rec, attr, []) or []:
                # A link's target is one token already -- `docs/a(b).md` included --
                # so only its `#anchor` goes and its length is left to check. Anything
                # else is unwrapped and must then be a single markup-free token.
                link = _MD_LINK.match(str(src))
                if link:
                    rel = link.group(1).split("#", 1)[0].split(":", 1)[0]
                    shaped = len(rel) <= _MAX_SOURCE_PATH
                else:
                    rel = _MARKUP_AROUND_PATH.sub("", str(src).split(":", 1)[0])
                    shaped = _path_shaped(rel)
                # Narrow on purpose: never `git:foo.md`, and a lesson's `seen_in` also
                # holds bug pins like "review-inverted-severity", which are not paths
                # -- reporting those as vanished files would be a false positive in the
                # check whose whole value is that its findings are real.
                looks_like_a_path = "/" in rel or rel.endswith(".md")
                if not rel or rel.startswith("git:") or not looks_like_a_path:
                    continue
                if shaped:
                    paths.setdefault(rel, []).append(rec.id)
                else:
                    prose += 1
    return prose


#: Longest free-form source still taken for a path. Far above any repository-relative
#: path a person writes, far below a paragraph.
_MAX_SOURCE_PATH = 1024
#: Markdown around a path in a `**Seen in:**` paragraph: "`nemorun/cli/export.py:88`."
#: is the file `nemorun/cli/export.py`, and reporting it vanished while it exists is a
#: false finding. A leading `.` is left alone -- `.ddflow/x.md` is a path.
_MARKUP_AROUND_PATH = re.compile(r"^[`'\"(\[<*]+|[`'\")\]>*.,;]+$")
#: A whole markdown link, `[docs/a.md](docs/a.md)`: its TARGET is the path -- also with
#: a title in any of CommonMark's three forms, and inside code/bold/emphasis markup with
#: punctuation on either side of it, `` `[a](docs/a.md 't').` ``, and a target with
#: balanced parentheses, `[a](docs/a(b).md)`.
_MD_LINK = re.compile(
    r"""^[`*_]*\[[^\]]*\]\(((?:[^()\s]|\([^()\s]*\))+)(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\)[`*_.,;]*$"""
)
#: Markup still inside a token once the wrapping is gone: not one path, but pieces.
_MARKUP_INSIDE = frozenset("`[]()<>*")


def _path_shaped(rel: str) -> bool:
    """One token that could name a file, not a sentence that mentions one (Bd5b59f3894).

    Only for the FREE-FORM fields `_memory_sources` reads: an imported lesson's `seen_in`
    is its corpus's `**Seen in:**` paragraph, and on a real corpus that is prose like
    "Phase 136, roborev 414-D4 ... `config/training/x.py` cited ~2839". It contains a
    `/`, so the old test took it for a path; a short one was reported as a vanished
    file, a long one crashed the stat. Item, note and memory sources are not filtered
    here: the importer writes them from real file names, which may contain spaces.
    """
    return len(rel) <= _MAX_SOURCE_PATH and not any(c.isspace() or c in _MARKUP_INSIDE for c in rel)


def _has_open_child(state, phase) -> bool:
    return any(not c.terminal and not c.removed for c in state.children(phase.id))


def verify_import(
    repo: Path,
    state,
    *,
    rescan: bool = True,
    sources: dict[str, tuple[str, ...]] | None = None,
    archive: tuple[str, ...] = (),
) -> VerifyReport:
    """Was the import done, is it still true, and did anyone finish it?

    ``rescan=False`` answers only from the folded queue and touches no file. That is
    the mode the MCP handshake uses: a full source scan is cheap for a command an
    operator typed and expensive for something that runs at every session start.
    """
    r = VerifyReport()
    ats, paths = _scan_queue(state, r)
    r.first_at, r.last_at = (min(ats), max(ats)) if ats else ("", "")

    if r.unstructured:
        # A NOTE, not a finding. Its own text says re-running does not fix it, and a
        # finding nobody can clear is a latch: `verified` could never go true again, and
        # a report that can never go green is one people stop reading.
        r.notes.append(
            f"{len(r.unstructured)} item(s) were imported before provenance was a "
            f"field, so what they came from is readable only as prose in their body. "
            f"Nothing fixes that; it is history."
        )
    if not r.total and not r.unstructured:
        r.notes.append(
            "Nothing in this queue records an import. If this project has history in "
            "docs/todo.md, a lessons corpus, ADRs, a research log, an engineering "
            "journal or a memory store, `ddflow import` proposes it and writes "
            "nothing until you pass --apply."
        )
        return r
    if not rescan:
        return r

    unstatable: list[str] = []
    for rel, ids in sorted(paths.items()):
        # os.stat, not Path.is_file: 3.11-3.13 RAISE on ENAMETOOLONG, 3.14 returns False
        # for every OSError -- a crash on one, an "unstatable" reported as vanished on
        # the other. Only "no such file" is a vanished file.
        try:
            present = stat.S_ISREG(os.stat(repo / rel).st_mode)
        except (FileNotFoundError, NotADirectoryError):
            present = False
        except (OSError, ValueError):
            # ENAMETOOLONG, an embedded NUL and kin: the filesystem refuses the string as
            # a path at all. Not a vanished file -- nothing ever existed under that name
            # -- and never a crash: one odd record must not cost the whole report.
            unstatable.append(rel)
            continue
        if not present:
            r.vanished.extend((i, rel) for i in sorted(ids))
    if unstatable:
        r.notes.append(
            f"{len(unstatable)} imported source(s) could not be checked, because the "
            f"filesystem does not accept them as a path: "
            + ", ".join(repr(s[:60]) for s in unstatable[:_NOTE_EXAMPLES])
            + (", ..." if len(unstatable) > _NOTE_EXAMPLES else "")
        )

    plan = plan_import(repo, state, max_tasks=10**9, sources=sources, archive=archive)
    r.drift = list(plan.found)
    r.empty_sources = list(plan.empty_sources)
    # Minus the note this report states in its own terms (history left out); the
    # empty-source note is in `plan.source_notes` and never reaches here.
    r.notes.extend(n for n in plan.notes if "already-ticked" not in n)
    r.notes.append(
        "Dependencies that do not resolve, duplicate globs and cycles are `ddflow "
        "doctor`'s job and it reports them in its own words -- this does not repeat "
        "them."
    )
    return r


__all__ = [
    "_MARKUP_AROUND_PATH",
    "_MARKUP_INSIDE",
    "_MAX_SOURCE_PATH",
    "_MD_LINK",
    "VerifyReport",
    "_cap_or_settle",
    "_has_open_child",
    "_memory_sources",
    "_note_sources",
    "_path_shaped",
    "_scan_queue",
    "plan_import",
    "verify_import",
]
