"""Phase settle/disposition, touch detection and scanner plumbing."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ...core.model import DONE, OPEN
from ._scan import (
    _NOTE_EXAMPLES,
    _attach_summaries,
    scan_decisions,
    scan_journal,
    scan_lesson_summaries,
    scan_lessons,
    scan_optmem,
    scan_research,
)
from ._tables import Found, ImportPlan, _unique
from ._titles import (
    _DONE_CAPS,
    _DONE_CAPS_ENDING,
    _DONE_CHECK,
    _DONE_CLAUSE,
    _NEGATED_BEFORE,
    _NEGATION,
    DECISION_GLOBS,
    JOURNAL_GLOBS,
    LESSON_GLOBS,
    LESSON_SUMMARY_GLOBS,
    OPTMEM_GLOBS,
    RESEARCH_GLOBS,
    TODO_GLOBS,
)
from ._todo import scan_todos


def _note_withheld(
    plan: ImportPlan, ticked: list[Found], closed: list[str], held: list[str]
) -> None:
    """Say what the import deliberately did not offer as work, and how to get it."""
    if ticked:
        # Per phase, worst first: the total alone left "Phase 103: 0/3" on the board
        # reading as "nothing shipped" about a phase whose 103.A and 103.C had (B45d5aa72fa).
        per_phase: dict[str, int] = {}
        for f in ticked:
            key = f.extra.get("phase") or "(no phase)"
            per_phase[key] = per_phase.get(key, 0) + 1
        worst = sorted(per_phase.items(), key=lambda kv: -kv[1])
        plan.notes.append(
            f"{len(ticked)} already-ticked task(s) were NOT imported, so the board counts "
            f"only the work left: a phase reading 0/3 may have shipped most of itself. "
            f"Most left out: "
            + ", ".join(f"{p} ({n})" for p, n in worst[:_NOTE_EXAMPLES])
            + (f", ... ({len(worst)} phases)" if len(worst) > _NOTE_EXAMPLES else "")
            + ". Re-run with --include-done (include_done over MCP) to bring them in as "
            "completed items -- which also completes every phase they finish."
        )
    if closed:
        # Named, like the held ones below: a bare count hid that two items under a
        # `(not started)` heading had been dropped with the history around them.
        plan.notes.append(
            f"{len(closed)} open task(s) were NOT imported because the source disposes "
            f"of them (declined, refuted, superseded, struck through, or in a section whose "
            f"STATUS says it is closed). They are history — pass include_done to bring them "
            f"in as abandoned items. E.g. {', '.join(closed[:_NOTE_EXAMPLES])}"
            + (", ..." if len(closed) > _NOTE_EXAMPLES else "")
            + "."
        )
    if held:
        plan.notes.append(
            f"{len(held)} open task(s) are marked deferred, theoretical or blocked (on the "
            f"item, its heading or its section's STATUS) and import as BLOCKED with that "
            f"reason: visible, never offered, and released with `ddflow unblock <id>`. "
            f"E.g. {', '.join(held[:_NOTE_EXAMPLES])}"
            + (", ..." if len(held) > _NOTE_EXAMPLES else "")
            + "."
        )
    if plan.empty_sources:
        # In the NOTES, not only in `empty_sources`: the human-readable proposal never
        # printed that field, so pointing `todo_globs` at a backlog written as bold
        # bullets instead of checkboxes printed a clean-looking proposal with nothing
        # from it, and the footer still said "the headings became phases".
        plan.source_notes.append(
            f"{len(plan.empty_sources)} file(s) matched a source pattern and yielded "
            f"NOTHING — usually an unusual format (a plan with no `- [ ]` checkboxes, "
            f"lessons with no headings) rather than an empty file: "
            f"{', '.join(plan.empty_sources[:_NOTE_EXAMPLES])}"
            + (", ..." if len(plan.empty_sources) > _NOTE_EXAMPLES else "")
        )


#: What the importer itself writes about an item: the add, carrying its source, and the
#: state the source gave it, flagged `imported`. Anything else on an item's subject is
#: somebody acting on it after the import.
_IMPORT_ADDS = frozenset({"phase.added", "task.added"})
_IMPORT_STATES = frozenset({"item.completed", "item.abandoned", "item.blocked"})
#: Every event that sets an item's state (see `core.model.HANDLERS`).
_STATE_EVENTS = _IMPORT_STATES | {"item.started", "item.unblocked"}


@dataclass
class Touched:
    """What somebody other than the importer did to the queue, per item id."""

    #: Any event at all on it that the importer did not write: claimed, gated, updated,
    #: blocked and released, completed -- or created by hand in the first place.
    items: set[str] = field(default_factory=set)
    #: Items whose CURRENT state was set by such an event: the last state change on
    #: them is somebody's, not the import's.
    state: set[str] = field(default_factory=set)


def _by_import(ev) -> bool:
    if ev.kind in _IMPORT_ADDS:
        return bool(ev.data.get("source"))
    return ev.kind in _IMPORT_STATES and bool(ev.data.get("imported"))


def touched_since_import(events) -> Touched:
    """Who acted on each item after (or instead of) an import, read from the log.

    What "already in the queue, left alone" has to mean once a re-run may COMPLETE an
    imported phase: the import may finish what it wrote and nobody has touched since, and
    nothing else. The fold cannot tell "open because nobody got to it" from "open because
    somebody reopened it"; the log can. `events` in log order, as `read_all` returns them.
    """
    t = Touched()
    for ev in events:
        mine = _by_import(ev)
        if not mine:
            t.items.add(ev.subject)
        if ev.kind in _STATE_EVENTS:
            (t.state.discard if mine else t.state.add)(ev.subject)
    return t


#: Words in a phase HEADING that say work remains, matched as whole words (`WIP` is not
#: `WIPE`). Two strengths. A STALLED word yields to a done marker in the same heading --
#: `Close the long-context PARTIAL ✅ SHIPPED` is finished, `DEFERRED` often says where
#: the work came from. A LIVE word does not: `Tooling (IN PROGRESS) ✅` contradicts
#: itself, and the side that hides work is the one not to pick (rubber-duck).
_HEADING_LIVE = re.compile(
    r"(?<![A-Z0-9])(IN PROGRESS|WIP|RE-?OPENED|UNFINISHED)(?![A-Z0-9])", re.I
)
_HEADING_STALLED = re.compile(
    r"(?<![A-Z0-9])(PARTIAL(?:LY)?|DEFERRED|ON HOLD|PARKED|BLOCKED)(?![A-Z0-9])", re.I
)
#: A done marker the heading itself negates: `NOT DONE`, `not yet shipped`.
_NEGATED_DONE = re.compile(rf"{_NEGATION}\W*(?:DONE|SHIPPED|CLOSED|COMPLETE[D]?)\b", re.I)


def _claims_done(title: str) -> bool:
    """Whether a heading says its work is finished: some status in it is not negated.

    One place for the drift note, `import --verify` and the phase verdict. Negation is
    per status, not per heading: `Phase 12 SHIPPED — docs NOT DONE` still claims done
    (so a phase shipped with a task open is still drift), `Phase 9 (NOT YET SHIPPED)`
    does not. Whether the heading ALSO says something remains is `_says_unfinished`'s
    question, built from the same `_NEGATION`: that one keeps the verdict from
    completing such a phase. In a heading written in
    capitals a capital status word must end its clause (`_DONE_CAPS_ENDING`).
    """
    letters = [c for c in title if c.isalpha()]
    caps_heading = bool(letters) and all(c.isupper() for c in letters)
    caps = _DONE_CAPS_ENDING if caps_heading else _DONE_CAPS
    starts = [m.start() for m in caps.finditer(title)]
    starts += [m.start("w") for m in _DONE_CLAUSE.finditer(title)]
    starts += [m.start() for m in _DONE_CHECK.finditer(title)]
    return any(not _NEGATED_BEFORE.search(title[:at]) for at in starts)


def _says_unfinished(phase: Found) -> str:
    """Why the phase's own words say work remains, or "" when they do not.

    Every box ticked is not the whole story: the project writes the rest in prose, and a
    real plan has `## Phase 1 — Tooling (IN PROGRESS)` and `**STATUS**: PARTIAL -- 11 of
    12` over nothing but ticked boxes. Completing those hides the one item left.
    """
    status = phase.extra.get("status")
    if status is not None and status[0] != "closed":
        return f"its STATUS says {status[1] or 'it is live'}"
    m = _NEGATED_DONE.search(phase.title) or _HEADING_LIVE.search(phase.title)
    if not m and not _claims_done(phase.title):
        m = _HEADING_STALLED.search(phase.title)
    return f"its heading says {m.group(0).upper()}" if m else ""


def _phase_verdict(
    phase: Found, pid: str, source: str, new_kids: list[Found], state, touched: Touched
) -> tuple[str, str]:
    """`(verdict, detail)` for one phase: `done`, `by_hand`, `unfinished` or `open`.

    Over EVERY task under it once this import has run: the ones it adds and the ones
    already in the queue. Done when each is done or closed; a phase with no task at all
    only when its own heading says finished. A queued task finished by somebody in
    ddflow rather than by the source makes it `by_hand`: closing the phase then is
    ddflow's own `complete`, with the phase gates, not the importer's. Boxes that all say
    finished under a heading or STATUS that says otherwise make it `unfinished`.
    """
    done = closed = 0
    by_hand = False
    for f in new_kids:
        if f.done:
            done += 1
        elif f.extra.get("disposition") == "closed":
            closed += 1
        else:
            return "open", ""
    for it in state.children(pid) if state is not None else ():
        if it.removed:
            continue
        if not it.terminal:
            return "open", ""
        if it.id in touched.state:
            by_hand = True
        elif it.state == DONE:
            done += 1
        else:
            closed += 1
    if by_hand:
        return "by_hand", ""
    if done + closed and _says_unfinished(phase):
        return "unfinished", _says_unfinished(phase)
    if done + closed:
        return "done", (
            f"every task under it is done or closed in the source ({done} done, "
            f"{closed} closed) -- phase imported from {source}"
        )
    if _claims_done(phase.title) and not _says_unfinished(phase):
        return "done", f"its heading at {source} marks it finished, and no task is filed under it"
    return "open", ""


@dataclass
class _Settled:
    """What `_settle_phases` decided, kept for the notes that say so."""

    new_done: int = 0
    completions: list[Found] = field(default_factory=list)
    left_alone: list[str] = field(default_factory=list)
    by_hand: list[str] = field(default_factory=list)
    unfinished: list[str] = field(default_factory=list)
    under_done: list[str] = field(default_factory=list)


def _is_imported_phase(it) -> bool:
    """A phase the IMPORTER wrote -- the only kind it may ever complete."""
    return it is not None and it.kind == "phase" and not it.removed and bool(it.source)


def _settle_existing(
    s: _Settled, existing: dict[str, Found], kids: dict[str, list[Found]], state, touched: Touched
) -> None:
    items = getattr(state, "items", {}) if state is not None else {}
    for pid, f in existing.items():
        it = items.get(pid)
        if not _is_imported_phase(it):
            continue
        if it.state == DONE:
            if any(
                not (k.done or k.extra.get("disposition") == "closed") for k in kids.get(pid, [])
            ):
                s.under_done.append(pid)
            continue
        if it.state != OPEN:
            continue
        verdict, detail = _phase_verdict(f, pid, it.source, kids.get(pid, []), state, touched)
        if verdict == "by_hand":
            s.by_hand.append(pid)
        elif verdict == "unfinished":
            s.unfinished.append(f"{pid} ({detail})")
        elif verdict == "done" and pid in touched.items:
            s.left_alone.append(pid)
        elif verdict == "done":
            s.completions.append(
                Found(
                    kind="completion",
                    ident=pid,
                    title=it.title,
                    source=it.source,
                    done=True,
                    extra={"evidence": detail},
                )
            )


def _settle_phases(plan: ImportPlan, state, touched: Touched, existing: dict[str, Found]) -> None:
    """Complete the phases the source says are finished -- decided AFTER the tasks.

    New phases in the plan are marked done in place. A phase already in the queue gets a
    `completion` entry, and only if the import wrote it, it is still OPEN and nobody has
    touched it since (`touched_since_import`); one that would qualify but was touched is
    NAMED, not completed. A task under it counts as finished by the SOURCE only while its
    state is still the one the import gave it. Never reopens anything.
    """
    kids: dict[str, list[Found]] = {}
    for f in plan.found:
        if f.kind == "task" and f.extra.get("phase"):
            kids.setdefault(f.extra["phase"], []).append(f)
    s = _Settled()
    for f in plan.by_kind("phase"):
        verdict, detail = _phase_verdict(
            f, f.ident, f.source, kids.get(f.ident, []), state, touched
        )
        if verdict == "done":
            f.done = True
            f.extra["evidence"] = detail
            s.new_done += 1
        elif verdict == "by_hand":
            s.by_hand.append(f.ident)
        elif verdict == "unfinished":
            s.unfinished.append(f"{f.ident} ({detail})")
    _settle_existing(s, existing, kids, state, touched)
    plan.found.extend(s.completions)
    # Completed, so no longer "left alone": the preview's count of those stays true.
    completed = {c.ident for c in s.completions}
    plan.skipped_existing = [i for i in plan.skipped_existing if i not in completed]
    _note_settled(plan, s)


def _settle_needed_phases(
    plan: ImportPlan,
    deferred: dict[str, Found],
    state,
    touched: Touched,
    existing: dict[str, Found],
) -> None:
    """Complete, on a PLAIN import, the finished phases that open work depends on.

    A plain import keeps such a phase (dropping it would leave the dependency unknown) but
    leaves its ticked tasks out -- so it landed EMPTY and OPEN, and the dependent was never
    offered: "phase P5 has no open tasks but is not marked done" (B-import-empty-needed-
    phase). The boxes under it are still the source's evidence that it is finished, so the
    verdict is the one `--include-done` reaches, read from the tasks this import left out;
    the tasks themselves stay out, as a plain import promises. Only phases that open work
    NEEDS, judged over every task under them: every other phase is exactly as it was. A phase already in the queue is completed under `_settle_existing`'s rules
    (imported, still open, untouched since), so a re-run releases work an earlier import
    left stuck.
    """
    needed = {d for f in plan.found for d in f.needs}
    # ...and what work ALREADY in the queue needs: on a re-run the dependent was imported
    # last time, so it is not in this plan, and it is the one stuck.
    for it in getattr(state, "items", {}).values():
        if not it.terminal and not it.removed:
            needed.update(it.needs)
    # Judged over EVERY task under the phase once this import has run -- the ones in this
    # plan (open ones keep it open; a finished one pulled in as a dependency counts as
    # done) and the ticked ones it leaves out -- plus, inside `_phase_verdict`, the ones
    # already queued. Skipping a phase because the plan held a task under it left it
    # empty-open whenever that task was itself a pulled-in dependency (roborev 959).
    planned = {id(f) for f in plan.found}
    kids: dict[str, list[Found]] = {}
    for f in plan.found:
        if f.kind == "task" and f.extra.get("phase"):
            kids.setdefault(f.extra["phase"], []).append(f)
    for f in deferred.values():
        if f.extra.get("phase") and id(f) not in planned:
            kids.setdefault(f.extra["phase"], []).append(f)
    s = _Settled()
    for f in plan.by_kind("phase"):
        if f.ident not in needed or f.done:
            continue
        verdict, detail = _phase_verdict(
            f, f.ident, f.source, kids.get(f.ident, []), state, touched
        )
        if verdict == "done":
            f.done = True
            f.extra["evidence"] = detail
            s.new_done += 1
        elif verdict == "unfinished":
            s.unfinished.append(f"{f.ident} ({detail})")
    stuck = {pid: f for pid, f in existing.items() if pid in needed}
    _settle_existing(s, stuck, kids, state, touched)
    plan.found.extend(s.completions)
    completed = {c.ident for c in s.completions}
    plan.skipped_existing = [i for i in plan.skipped_existing if i not in completed]
    _note_settled(plan, s)


def _note_settled(plan: ImportPlan, s: _Settled) -> None:
    def named(ids: list[str]) -> str:
        return ", ".join(ids[:_NOTE_EXAMPLES]) + (", ..." if len(ids) > _NOTE_EXAMPLES else "")

    if s.new_done or s.completions:
        plan.notes.append(
            f"{s.new_done} new phase(s) and {len(s.completions)} already in the queue are "
            f"finished in the source -- every task under them done or closed, or no task "
            f"and a heading that says so -- and import COMPLETED, with that as evidence."
        )
    if s.left_alone:
        plan.notes.append(
            f"{len(s.left_alone)} imported phase(s) are finished in the source but were left "
            f"open because someone changed them after the import: {named(s.left_alone)}. "
            f"`ddflow complete <id>` if they are done."
        )
    if s.by_hand:
        plan.notes.append(
            f"{len(s.by_hand)} phase(s) have every task finished, some of them in ddflow "
            f"rather than in the source, so the import does not close them -- that is "
            f"`ddflow complete <phase>`, with its phase gates: {named(s.by_hand)}."
        )
    if s.unfinished:
        plan.notes.append(
            f"{len(s.unfinished)} phase(s) have every box ticked or closed while their own "
            f"heading or STATUS says work remains, so they stay OPEN: {named(s.unfinished)}. "
            f"Ask the operator which is stale; `ddflow complete <id>` if they are done."
        )
    if s.under_done:
        plan.notes.append(
            f"{len(s.under_done)} phase(s) are DONE in the queue while this import adds open "
            f"work under them: {named(s.under_done)}. Reopen the phase or re-home the work."
        )


#: A scanner: repo and globs in, `(found, files that matched and yielded nothing)` out.
Scanner = Callable[..., tuple[list[Found], list[str]]]


def _scanners() -> tuple[tuple[str, Scanner, tuple[str, ...]], ...]:
    """family -> (scanner, the globs it reads when `[importer] <family>_globs` is empty).

    ONE table: `plan_import`, the handshake's "is there anything to import?" count and the
    config knobs all read it, so a family added here is configurable and counted without
    anyone remembering to. A function rather than a constant only because the scanners are
    defined above it and the table must follow them.
    """
    return (
        ("todo", scan_todos, TODO_GLOBS),
        ("lesson", scan_lessons, LESSON_GLOBS),
        ("lesson_summary", scan_lesson_summaries, LESSON_SUMMARY_GLOBS),
        ("decision", scan_decisions, DECISION_GLOBS),
        ("research", scan_research, RESEARCH_GLOBS),
        ("journal", scan_journal, JOURNAL_GLOBS),
        ("memory", scan_optmem, OPTMEM_GLOBS),
    )


def sources_from(cfg) -> dict[str, tuple[str, ...]]:
    """The globs each family reads under this config: the knob when set, else the default.

    A set knob REPLACES the default rather than extending it. That is what lets a project
    whose `docs/LOG.md` is a generated index of its real journal leave it out, and a
    project whose `docs/LOG.md` IS the journal put it in -- the same filename meaning
    opposite things in the two repositories this was built against.

    Spelled out knob by knob rather than read with `getattr(f"{family}_globs")`: a
    family added to `_scanners()` with no knob behind it is then a KeyError here, not a
    silently unconfigurable source.
    """
    imp = cfg.importer
    configured = {
        "todo": imp.todo_globs,
        "lesson": imp.lesson_globs,
        "lesson_summary": imp.lesson_summary_globs,
        "decision": imp.decision_globs,
        "research": imp.research_globs,
        "journal": imp.journal_globs,
        "memory": imp.memory_globs,
    }
    return {
        family: tuple(configured[family] or ()) or default for family, _scan, default in _scanners()
    }


def all_source_globs(cfg=None) -> tuple[str, ...]:
    """Every file pattern the scanners read under `cfg` (the defaults without one)."""
    srcs = sources_from(cfg) if cfg is not None else {f: d for f, _s, d in _scanners()}
    return tuple(g for globs in srcs.values() for g in globs)


def _scan_sources(
    repo: Path, plan: ImportPlan, sources: dict[str, tuple[str, ...]], archive: tuple[str, ...]
) -> list[Found]:
    """Run every scanner over its globs; what it found, with the summaries attached."""
    scanned: list[Found] = []
    for family, scan, default in _scanners():
        globs = sources.get(family) or default
        items, empty = scan(repo, globs, archive) if family == "todo" else scan(repo, globs)
        plan.empty_sources.extend(empty)
        scanned.extend(items)
    _attach_summaries(scanned, plan)
    return scanned


def _keep_imported_ids(scanned: list[Found], known: set[str]) -> None:
    """An item already in the queue under the id an older import derived keeps that id.

    Ids are minted from the text, and the rule changed (whole words, not 24 characters),
    so the same line now derives a different id. Without this a re-import reads every such
    item as new, and the children of such a phase get a parent that does not exist."""
    renamed: dict[str, str] = {}
    claimed: set[str] = set()
    for f in scanned:
        old = f.extra.get("legacy_ident")
        if f.kind == "phase" and old and old in known and f.ident not in known:
            if old in claimed:
                continue
            claimed.add(old)
            renamed[f.ident] = old
            f.ident = old
    for f in scanned:
        if f.kind != "task":
            continue
        phase = f.extra.get("phase", "")
        if phase in renamed:
            f.extra["phase"] = renamed[phase]
            if f.ident.startswith(f"{phase}."):
                f.ident = renamed[phase] + f.ident[len(phase) :]
        old = f.extra.get("legacy_ident")
        if old and old in known and f.ident not in known and old not in claimed:
            claimed.add(old)
            f.ident = old


def _triage_found(
    plan: ImportPlan,
    scanned: list[Found],
    known: set[str],
    proposed: set[str],
    include_done: bool,
) -> tuple[list[Found], list[str], dict[str, Found], dict[str, Found]]:
    """Sort what the scanners found into the plan, the already-imported, and the
    finished-or-closed items withheld. (closed, held, deferred_done, existing_phases)."""
    closed: list[Found] = []
    held: list[str] = []
    deferred_done: dict[str, Found] = {}
    existing_phases: dict[str, Found] = {}
    _keep_imported_ids(scanned, known)
    for f in scanned:
        # Uniquify BEFORE the already-imported check, and against what THIS scan
        # proposed rather than against what is in the queue. Both halves matter:
        #
        # Ids are the queue's primary key, and a collision is silent data loss --
        # the second event folds over the first, one item disappears, and the
        # import reports both as written. Two lessons whose titles agree in their
        # first 32 characters collide; a real corpus had 42 such pairs. Doing it
        # once here rather than inside each scanner is six private `taken` sets
        # avoided, and cross-scanner collisions caught.
        #
        # Seeding from `known` instead would make the id depend on what had
        # already been imported: the pair that became `L-x` and `L-x-2` on the
        # first run would come out `L-x-2`, `L-x-3` on the second, and an import
        # advertised as idempotent would duplicate its whole corpus on every run.
        f.ident = _unique("", f.ident, proposed)
        if f.ident in known or f.extra.get("legacy_ident") in known:
            plan.skipped_existing.append(f.ident)
            if f.kind == "phase":
                existing_phases[f.ident] = f
            continue
        if f.kind == "task" and f.done and not include_done:
            deferred_done[f.ident] = f
            continue
        if f.kind == "task" and f.extra.get("disposition") == "closed" and not include_done:
            # Declined, refuted, superseded: history, the same as a ticked box -- and
            # brought in the same way when open work depends on it.
            deferred_done[f.ident] = f
            closed.append(f)
            continue
        if f.kind == "task" and f.extra.get("disposition") == "hold":
            held.append(f.ident)
        plan.found.append(f)
    return closed, held, deferred_done, existing_phases


__all__ = [
    "_HEADING_LIVE",
    "_HEADING_STALLED",
    "_IMPORT_ADDS",
    "_IMPORT_STATES",
    "_NEGATED_DONE",
    "_STATE_EVENTS",
    "Scanner",
    "Touched",
    "_Settled",
    "_by_import",
    "_claims_done",
    "_is_imported_phase",
    "_keep_imported_ids",
    "_note_settled",
    "_note_withheld",
    "_phase_verdict",
    "_says_unfinished",
    "_scan_sources",
    "_scanners",
    "_settle_existing",
    "_settle_needed_phases",
    "_settle_phases",
    "_triage_found",
    "all_source_globs",
    "sources_from",
    "touched_since_import",
]
