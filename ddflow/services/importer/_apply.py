"""`apply_import`: write the planned import to the queue."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...config import Config
from ...core.model import Item, fold
from ...infra.log import EventLog
from .. import similar
from ..items import TaskDraft, add_task
from ._scan import _NOTE_EXAMPLES
from ._tables import NOTE_SESSIONS, Duplicate, Found, ImportPlan
from ._titles import _BODY_CHARS


def _apply_state(log: EventLog, f: Found, bump: Callable[[str], None]) -> None:
    """The state an imported task lands in, beyond `task.added`: done, closed or held.

    Each carries its SOURCE in the reason -- ddflow does not invent completion, and a
    held item that cannot say why it is held is one nobody can decide to release.
    """
    if f.done:
        log.append(
            "item.completed",
            f.ident,
            {"kind": "task", "imported": True, "evidence": f"ticked in {f.source}"},
        )
        bump("task_done")
    elif f.extra.get("disposition") == "closed":
        log.append(
            "item.abandoned",
            f.ident,
            {
                "kind": "task",
                "imported": True,
                "reason": f"{f.extra.get('disposition_why', 'closed')} in {f.source}",
            },
        )
        bump("task_closed")
    elif f.extra.get("disposition") == "hold":
        log.append(
            "item.blocked",
            f.ident,
            {
                "kind": "task",
                "imported": True,
                "reason": (
                    f"{f.extra.get('disposition_why', 'held')} in {f.source}. "
                    f"`ddflow unblock {f.ident}` when it becomes work."
                ),
            },
        )
        bump("task_held")


def _resolve_chains(dropped: dict[int, Duplicate], in_plan: dict[str, Found]) -> None:
    """A summary bullet that repeats a record of this import which is itself left out
    repeats what THAT repeats: point at the record that is actually held, so the outcome
    does not depend on the order the records were read."""
    # One hop is all there is: only a summary-born lesson targets a record of this import,
    # and that record is never summary-born, so it was dropped against the queue.
    for d in dropped.values():
        nxt = dropped.get(id(in_plan[d.of])) if d.where == "import" else None
        if nxt is not None:
            d.of, d.where = nxt.of, nxt.where
            # Word for word only if every hop was; the score is the weakest hop's.
            d.identical = d.identical and nxt.identical
            d.score = min(d.score, nxt.score)


#: Identical repeats named in the plan's note (the rest are counted).
_SHOWN_IDENTICAL = 12


def _dedupe_found(repo: Path, state, plan: ImportPlan) -> None:
    """Leave out what repeats a record already held, and say so. In place.

    Decision D-no-duplicates, applied to the importer. Checked: lessons, decisions and
    research -- the knowledge an onboarding brings in. Tasks and phases are not: they
    carry dependencies, and withholding one would leave the rest waiting on an id the
    queue never heard of. Each is weighed against every record already in the log
    (`services.similar`, the same engine and thresholds `[dedupe]` sets for an add), and
    a lessons-summary bullet that became a lesson of its own is also weighed against the
    other records of this import -- that is how a hand-written summary repeats the
    corpus beside it.

    Identical text is dropped quietly (counted). A near-duplicate -- at `ask_threshold`
    with enough content words -- is dropped from the proposal and LISTED, with the id it
    repeats and the score, because no score separates a duplicate from a related record
    and the operator answers that. Naming an existing id does not count: an imported
    lesson citing `L12` is not a copy of it.
    """

    cfg = Config.load(repo)
    dd = cfg.dedupe
    checked = ("lesson", "decision", "research")
    if dd.on_match == "off":
        return
    mine = [f for f in plan.found if f.kind in checked and f.kind in dd.kinds]
    if not mine:
        return

    # Another record of this import is a target only for a summary-born lesson, and a
    # target must outrank it: a summary bullet repeats the corpus, never the reverse.
    # Two non-summary records of one import are NOT compared with each other, on purpose:
    # which of a pair to keep is the author's call (both are kept), and dropping the later
    # would make the outcome depend on the order the files were read.
    def is_summary(f: Found) -> bool:
        return f.kind == "lesson" and "summary" in f.extra.get("tags", ())

    in_plan = {f.ident: f for f in mine}
    records = [
        {"id": f.ident, "kind": f.kind, "title": f.title, "body": f.body, "item": ""} for f in mine
    ]
    _, repeats = similar.screen(
        records,
        state,
        cfg,
        compare=lambda rec, tgt: (
            is_summary(in_plan[rec["id"]]) and not is_summary(in_plan[tgt["id"]])
        ),
    )
    dropped = {
        id(mine[h.index]): Duplicate(mine[h.index], h.of, h.score, h.identical, h.where)
        for h in repeats
    }
    if not dropped:
        return
    _resolve_chains(dropped, in_plan)
    warn = dd.on_match == "warn"
    if not warn:
        plan.found = [f for f in plan.found if id(f) not in dropped]
    plan.duplicates = [dropped[id(f)] for f in mine if id(f) in dropped]
    same = [d for d in plan.duplicates if d.identical]
    near = [d for d in plan.duplicates if not d.identical]
    if same:
        plan.notes.append(
            f"{len(same)} record(s) repeat another word for word and "
            f"{'WILL be imported anyway ([dedupe].on_match = warn)' if warn else 'were not imported'}: "
            + ", ".join(
                f"{d.found.ident} = {d.of}" + (" (in this import)" if d.where == "import" else "")
                for d in same[:_SHOWN_IDENTICAL]
            )
            + (" ..." if len(same) > _SHOWN_IDENTICAL else "")
        )
    if near:
        lines = [
            f"{len(near)} record(s) look like ones already held and "
            f"{'WILL be imported anyway ([dedupe].on_match = warn)' if warn else 'were NOT imported'} -- "
            f"decide each (file one anyway with the matching `ddflow <kind> add`, answering "
            f"its duplicate check with --new; "
            f"otherwise the existing record already says it). Candidate and score:"
        ]
        lines += [
            f"    {d.found.kind} {d.found.ident} ~ {d.of} ({d.score:.2f}"
            f"{', in this import' if d.where == 'import' else ''})  {d.found.source}"
            for d in sorted(near, key=lambda d: (-d.score, d.found.ident))
        ]
        plan.notes.append("\n".join(lines))


def _memories_within(repo: Path, plan: ImportPlan) -> tuple[list[Found], list[Found], int]:
    """``(kept, too_long, limit)``: the plan's memories within `[memory] max_chars`, and
    the rest.

    Refused over the limit, never cut: the rule `memory add` and the onboarding harness
    apply -- a memory cut mid-sentence says something its author did not (B021a859d56).
    The plan leaves them out with a note, so a re-scan does not report them as drift.
    """
    limit = Config.load(repo).memory.max_chars
    found = plan.by_kind("memory")
    return (
        [f for f in found if len(f.body) <= limit],
        [f for f in found if len(f.body) > limit],
        limit,
    )


def _leave_out_long_memories(repo: Path, plan: ImportPlan) -> None:
    """Drop the plan's memories over `[memory] max_chars`, with a note naming each."""
    _, too_long, limit = _memories_within(repo, plan)
    if too_long:
        plan.found = [f for f in plan.found if f not in too_long]
        plan.long_memories_left_out = len(too_long)
        named = ", ".join(f"{f.ident} ({f.source})" for f in too_long[:_NOTE_EXAMPLES])
        more = ", ..." if len(too_long) > _NOTE_EXAMPLES else ""
        plan.notes.append(
            f"{len(too_long)} memory record(s) over [memory] max_chars ({limit}) not "
            f"imported, never cut: {named}{more}. Write each as a lesson, or shorten it "
            f"in its source."
        )


def _count_long_memories(counts: dict[str, int], left_out: int, limit: int) -> None:
    if left_out:
        counts[f"memory over [memory] max_chars ({limit}) not recorded"] = left_out


def _imported_research(f: Found) -> dict[str, Any]:
    """The `research.recorded` payload for a scraped research entry.

    A scraped CONFIRMED/REFUTED has no probe behind it, which `research add` refuses as
    an opinion wearing a label: it is imported as THEORETICAL, the source's label kept
    as a tag, and the claim whole rather than cut to 600 characters (B021a859d56).
    """
    scraped = f.extra.get("verdict", "THEORETICAL")
    tags = ["imported"] + ([f"source-verdict:{scraped}"] if scraped != "THEORETICAL" else [])
    return {
        "question": f.title,
        "claim": f.body,
        "verdict": "THEORETICAL",
        "sources": [f.source],
        "tags": tags,
    }


def task_body(f: Found) -> str:
    """An imported task's body: the item's own text, then where it came from."""
    text = f.body.strip()[:_BODY_CHARS]
    return (f"{text}\n\n" if text else "") + f"Imported from {f.source}."


def _add_imported_tasks(
    log: EventLog, st: Any, cfg: Config, plan: ImportPlan, bump: Callable[[str], None]
) -> set[str]:
    """Write the plan's tasks, each held to the checks every new task passes (`add_task`),
    then its state; and its branches. Returns the ids the checks refused -- none of those
    is written, and nothing is recorded about it."""
    refused: set[str] = set()
    for f in plan.by_kind("task"):
        draft = TaskDraft(
            f.ident,
            parent=f.extra.get("phase", ""),
            title=f.title,
            needs=f.needs,
            globs=f.globs,
            resources=f.extra.get("resources", []),
            body=task_body(f),
            # Both: the prose is what a human reads in `ddflow show`, the field is
            # what `import --verify` counts. Deriving one from the other by regex is
            # how a reworded sentence silently zeroes a verification.
            extra={"source": f.source},
        )
        if not add_task(log, st, cfg, draft, dedupe=_SCREENED).ok:
            refused.add(f.ident)
            continue
        bump("task")
        _apply_state(log, f, bump)
    for f in plan.by_kind("branch"):
        draft = TaskDraft(
            f.ident,
            title=f.title,
            body=(
                f"Imported from {f.source}: this branch carries "
                f"{f.extra.get('ahead', '?')} commit(s) not on the base branch. "
                f"Declare its globs before anyone claims it."
            ),
            extra={"source": f.source},
        )
        if not add_task(log, st, cfg, draft, dedupe=_SCREENED).ok:
            refused.add(f.ident)
            continue
        bump("branch")
    return refused


#: Why an imported task is not checked again when it is written.
_SCREENED = "the plan was screened against the queue before apply (tasks carry dependencies)"

#: The count of imported tasks and branches the item checks refused (`apply_import`).
_REFUSED_KEY = "task(s) refused by the item checks (id, globs, parent or already queued)"


def apply_import(repo: Path, log: EventLog, plan: ImportPlan) -> dict[str, int]:
    """Write the proposal to the log. Called only after someone has looked at it.

    Tasks marked done are recorded as done **with their source**, not silently: the
    evidence is "a human ticked this box in docs/todo.md:41", which is exactly what it
    is, and a reader can go and check. ddflow does not invent completion.
    """
    counts: dict[str, int] = {}

    def bump(kind: str) -> None:
        counts[kind] = counts.get(kind, 0) + 1

    cfg = Config.load(repo)
    st = fold(log.read_all(), strict=False)
    for f in plan.by_kind("phase"):
        log.append(
            "phase.added",
            f.ident,
            {"title": f.title, "body": f"Imported from {f.source}.", "source": f.source},
        )
        st.items[f.ident] = Item(id=f.ident, kind="phase", title=f.title)
        bump("phase")
    refused = _add_imported_tasks(log, st, cfg, plan, bump)
    # A phase whose task the checks refused is not finished by what is under it: it stays
    # open, and the refused count says why.
    held = {t.extra.get("phase", "") for t in plan.by_kind("task") if t.ident in refused}
    # Phases LAST: finished by what is under them, so only once that is written -- a
    # reader of the log never sees a phase done over tasks that do not exist yet.
    for f in [p for p in plan.by_kind("phase") if p.done] + plan.by_kind("completion"):
        if f.ident in held:
            continue
        log.append(
            "item.completed",
            f.ident,
            {"kind": "phase", "imported": True, "evidence": f.extra.get("evidence", "")},
        )
        bump("phase_done" if f.kind == "phase" else "completion")
    for f in plan.by_kind("lesson"):
        data: dict[str, Any] = {
            "title": f.title,
            "rule": f.body,
            "tags": ["imported", *f.extra.get("tags", [])],
            "seen_in": [f.source, *f.extra.get("seen_in", [])],
        }
        if f.extra.get("summary"):
            data["summary"] = f.extra["summary"]
        log.append("lesson.recorded", f.ident, data)
        bump("lesson")
    for f in plan.by_kind("decision"):
        log.append(
            "decision.recorded",
            f.ident,
            {
                "title": f.title,
                "decision": f.body,
                "status": f.extra.get("status", "accepted"),
                "context": f"Imported from {f.source}.",
                "tags": ["imported"],
                "sources": [f.source],
            },
        )
        bump("decision")
    # Journal entries and OptMem records are both "a record of something that was
    # true", which is what a session note IS. One session each rather than one per
    # entry: N synthetic sessions would bury the real ones in `replay`.
    for kind, sid, what in NOTE_SESSIONS:
        entries = plan.by_kind(kind)
        if not entries:
            continue
        log.append("session.started", sid, {"model": "(imported)", "tool": "ddflow import"})
        for n, f in enumerate(entries):
            log.append(
                "session.note",
                sid,
                {
                    "seq": n,
                    "ident": f.ident,
                    "text": (
                        f.body
                        if f.extra.get("title_is_excerpt")
                        else f"{f.title}\n\n{f.body}".strip()
                    )[:4000],
                    "at": f.extra.get("at", ""),
                    "source": f.source,
                },
            )
            bump(kind)
        log.append(
            "session.ended",
            sid,
            {"summary": f"Imported {len(entries)} {what} from this project."},
        )
    # OptMem records are operational MEMORIES -- the thing `brief` shows first and
    # `recall` searches -- not journal notes. Their store numbered them, so the id is
    # the store's (`M-0041`) and a re-import skips what is already remembered.
    # `plan_import` left them out and counted them; a plan built by hand gets the rule here.
    kept, too_long, limit = _memories_within(repo, plan)
    _count_long_memories(counts, len(too_long) + plan.long_memories_left_out, limit)
    for f in kept:
        log.append(
            "memory.recorded",
            f.ident,
            {
                "text": f.body,
                "origin_at": f.extra.get("at", ""),
                "source": f.source,
                "tags": ["imported"],
            },
        )
        bump("memory")
    for f in plan.by_kind("research"):
        log.append("research.recorded", f.ident, _imported_research(f))
        bump("research")
    if refused:
        counts[_REFUSED_KEY] = len(refused)
    if plan.ticked_left_out:
        # Not written -- LEFT OUT, and said in the one line `--apply` prints, because
        # otherwise the report of what landed is read as the whole story (B45d5aa72fa).
        counts["ticked task(s) left out, see --include-done"] = plan.ticked_left_out
    return counts


__all__ = [
    "_REFUSED_KEY",
    "_SCREENED",
    "_SHOWN_IDENTICAL",
    "_add_imported_tasks",
    "_apply_state",
    "_count_long_memories",
    "_dedupe_found",
    "_imported_research",
    "_leave_out_long_memories",
    "_memories_within",
    "_resolve_chains",
    "apply_import",
    "task_body",
]
