"""What the project remembers: lessons, research, bugs, sessions, and the history.

Three refusals here are the reason this family is policy rather than plumbing, and each
exists because the record is worthless without it:

* **A research verdict must be CONFIRMED, REFUTED or THEORETICAL**, and the first two
  need a probe. A verdict with no probe behind it is an opinion, and a note with no
  verdict is a literature summary.
* **A bug may not be closed without naming the regression test** that would catch it
  again — write the test, watch it FAIL against the unfixed code, then close.
* **Recall is a prompt to CHECK, not a verdict.** That sentence ships in the output
  because the failure mode is an agent treating a three-week-old prompt as binding.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ..config import Config, csv_list
from ..core import globspec as GS
from ..core import outcome as O
from ..core.events import parse_changelog
from ..core.ids import auto_id
from ..core.model import LINK_RELATIONS, fold
from ._base import _load

VERDICTS = ("CONFIRMED", "REFUTED", "THEORETICAL")


def _store(repo, log, cfg):
    from ..infra.store import Store

    s = Store(repo, cfg)
    s.ensure(log)
    return s


@dataclass
class LessonDraft:
    """The lesson record, named once.

    Same reasoning as `decisions.Draft`: these fields are spelled out three times — the
    argparse flags, the MCP input schema and the event payload — and a field added to one is
    a field the other two silently drop. B20's `pattern` and `globs` are exactly such an
    addition, so they arrive as a type rather than as two more positional-ish keywords.

    `tags`, `seen_in`, `supersedes` and `globs` are comma-separated strings because that is
    what an argparse flag and a JSON string field both give you; `csv_list` is the ONE
    parser for that notation.
    """

    title: str
    rule: str = ""
    why: str = ""
    how: str = ""
    #: The one-paragraph form, rendered into `LESSONS-SUMMARY.md`.
    summary: str = ""
    tags: str = ""
    seen_in: str = ""
    supersedes: str = ""
    #: B20: the pattern this lesson forbids, and where to look for it. With a pattern, the
    #: repository is scanned AT FILING TIME and the matching sites are stored.
    pattern: str = ""
    globs: str = ""
    id: str = ""
    #: What the adder says about a possible duplicate (``_dedupe.Answer``).
    answer: DD.Answer | None = None


def lesson_add(repo: Path, draft: LessonDraft, *, agent: str = "") -> O.Outcome:
    """Record a transferable rule — the pattern, not the incident.

    With ``pattern``, the repository is SCANNED NOW and the matching sites are stored with
    the lesson (B20). That is the whole mechanism: `ddflow lesson verify` re-scans later
    and names which sites appeared, where a stored count could only say that things got
    worse. A count cannot be acted on and cannot be reviewed.
    """
    from ..services import inventory as INV

    log, cfg, st = _load(repo, agent)
    lid = draft.id or auto_id("L", draft.title, draft.rule)
    data: dict[str, Any] = {
        "title": draft.title,
        "rule": draft.rule,
        "why": draft.why,
        "how": draft.how,
        "tags": csv_list(draft.tags),
        "seen_in": csv_list(draft.seen_in),
        "supersedes": csv_list(draft.supersedes),
    }
    if draft.summary:
        data["summary"] = draft.summary
    sites: list[str] = []
    if draft.pattern:
        globlist = csv_list(draft.globs)
        try:
            sites = INV.scan(repo, draft.pattern, globlist)
        except INV.PatternError as exc:
            # REFUSED, not recorded with an empty inventory. A lesson whose pattern does not
            # compile would store zero sites, read as "the code is clean", and ratchet every
            # real occurrence away the first time anybody ran the check.
            return O.failed("lesson.pattern_invalid", str(exc), pattern=draft.pattern)
        data |= {"pattern": draft.pattern, "globs": globlist, "sites": sites}
    chk = DD.check_add(
        repo,
        log,
        cfg,
        st,
        DD.Record(
            kind="lesson",
            event_kind="lesson.recorded",
            rid=lid,
            title=draft.title,
            body="\n".join(x for x in (draft.rule, draft.why, draft.how) if x),
        ),
        draft.answer,
    )
    if chk.refusal is not None:
        return chk.refusal
    if chk.extension:
        return DD.extend(log, cfg, chk, "lesson.recorded")
    with log.transaction():
        log.append("lesson.recorded", lid, data | chk.fields)
        DD.after_add(log, cfg, lid, chk)
    return O.ok("lesson.recorded", id=lid, sites=len(sites), inventory=sites, **chk.data())


def lessons_verify(repo: Path, *, agent: str = "") -> O.Outcome:
    """Re-scan every lesson that declares a pattern, and name what reappeared (B20).

    Three exit codes, none collapsible:

    * ``0`` — every inventory still matches the code.
    * ``1`` — at least one forbidden pattern appeared at a NEW site. The sites are listed,
      because that is the whole point: a count says "worse" and never "which".
    * ``2`` — no lesson declares a pattern, so there is nothing mechanical to check. Not a
      pass: reporting "all clear" for a corpus with zero ratchets is how a project convinces
      itself it has checks it does not have.
    """
    from ..services import inventory as INV

    _log, _cfg, st = _load(repo, agent)
    live = {k: v for k, v in st.lessons.items() if not v.superseded_by}
    checkable = INV.checkable(st.lessons)
    if not checkable:
        nothing_checked = (
            f"No lesson declares a pattern, so nothing was checked ({len(live)} live "
            f"lesson(s)). Add `--pattern` to a lesson whose mistake is mechanical."
        )
        # `text` on EVERY branch: the tool declares `payload: "text"`, so a branch that
        # omits it raises KeyError out of `Outcome.body` rather than returning the exit-2
        # answer. Found by `test_a_migrated_tool_reproduces_its_CLI_json_exactly`, which
        # exercises each tool's wire shape — the exit-2 path had no other caller.
        return O.nothing(
            "lessons.verify", nothing_checked, checked=0, live=len(live), text=nothing_checked
        )
    diffs = [d for d in (INV.diff(repo, x) for x in checkable.values()) if d is not None]
    # Through `regressions()`, not a second copy of its filter: it owns "which lessons are
    # still in force and have come back", and a duplicate here would be a second answer to
    # the same question. (It WAS a duplicate, and the mutation run caught it: breaking
    # `regressions()` changed nothing because nothing called it.)
    regressed = INV.regressions(repo, checkable)
    data: dict[str, Any] = {
        "checked": len(diffs),
        "live": len(live),
        "regressed": [
            {"lesson": d.lesson, "appeared": d.appeared, "gone": d.gone} for d in regressed
        ],
        "fixed": [
            {"lesson": d.lesson, "gone": d.gone} for d in diffs if d.gone and not d.regressed
        ],
        "text": "\n".join(d.render() for d in diffs) or "nothing to report",
    }
    if regressed:
        return O.failed(
            "lessons.verify",
            f"{len(regressed)} lesson(s) have new sites: " + "; ".join(d.lesson for d in regressed),
            **data,
        )
    return O.ok("lessons.verify", **data)


def lesson_search(
    repo: Path, query: str, *, limit: int | None = None, agent: str = ""
) -> O.Outcome:
    log, cfg, _st = _load(repo, agent)
    hits = _store(repo, log, cfg).search(
        "lessons", query, limit if limit is not None else cfg.lessons.max_results
    )
    data: dict[str, Any] = {
        "hits": hits,
        "count": len(hits),
        "query": query,
        "snippet_chars": cfg.lessons.snippet_chars,
    }
    if not hits:
        return O.nothing("lesson.search", "no matching lessons", **data)
    return O.ok("lesson.search", **data)


def _wire_hit(table: str, label: str, r: dict) -> dict:
    """One hit as the `--json` / MCP body carries it.

    A decision, lesson or memory is somebody's words: its headline and body travel inside
    the data fence with the author and trust (`core/provenance.py`), and the headline
    outside it is only the id and the provenance sentence. JSON quoting is not a fence --
    an agent reads the string, not the quotes -- so this is the surface that matters most.
    """
    from ..core import provenance as PV
    from ..infra.store import summarise_row

    head, body = summarise_row(table, r)
    hit: dict[str, Any] = {"id": r.get("id"), "kind": label, "headline": head, "body": body}
    prov = r.get("provenance")
    if prov:
        origin = PV.Origin(prov["trust"], prov["by"], prov["source"])
        kind = PV.TABLE_KIND[table]
        hit["headline"] = f"{r.get('id')} ({origin.label()})"
        hit["body"] = PV.fence(kind, str(r.get("id")), head + (f": {body}" if body else ""), origin)
        hit["provenance"] = prov
    hit["raw"] = r
    return hit


def _origin(st, table: str, ident):
    """The `Origin` of a recalled decision, lesson or memory; None for the other kinds."""
    from ..core import provenance as PV

    if table == "decisions" and ident in st.decisions:
        return PV.decision_origin(st.decisions[ident])
    if table == "lessons" and ident in st.lessons:
        return PV.lesson_origin(st.lessons[ident])
    if table == "memories" and ident in st.memories:
        return PV.memory_origin(st.memories[ident])
    return None


def recall(
    repo: Path,
    query: str,
    *,
    sources: str = "",
    limit: int = 3,
    max_chars: int = 4000,
    agent: str = "",
) -> O.Outcome:
    """ "Have we been here before?" — one query across everything the project remembers.

    Searches architectural decisions, lessons, research verdicts, past bugs, similar
    tasks and the operator's own earlier prompts, and labels each hit by WHAT KIND of
    thing it is — because "should this change what I do" has a different answer for a
    binding decision, a transferable lesson and a prompt from three weeks ago.

    Exists so an operator does not have to say the same thing twice and an agent does not
    have to learn the same thing twice. Both failures are invisible in the moment and
    obvious in the log.
    """
    from ..infra.store import RECALL_SOURCES

    log, cfg, _st = _load(repo, agent)
    store = _store(repo, log, cfg)
    want = csv_list(sources) or [t for t, _, _ in RECALL_SOURCES]
    lowered = [w.lower() for w in want]
    results: dict[str, list[dict]] = {}
    for table, label, _why in RECALL_SOURCES:
        if table not in want and label.lower() not in lowered:
            continue
        try:
            hits = store.search(table, query, limit)
        except Exception:
            # One unreadable source must not take the whole recall down: the value is in
            # the union, and "the lessons table is corrupt" is not a reason to withhold
            # the decisions.
            hits = []
        if hits:
            results[table] = hits

    labels = {table: label for table, label, _ in RECALL_SOURCES}
    # Who recorded each hit (`core/provenance.py`): decisions, lessons and memories are
    # somebody's words, and a hit shown without its author reads as the tool's own.
    # Looked up in the folded state by id -- the index holds no author column.
    for table, rows in results.items():
        for r in rows:
            o = _origin(_st, table, r.get("id"))
            if o is not None:
                r["provenance"] = {"trust": o.trust, "by": o.by, "source": o.source}
    wire = {
        table: [_wire_hit(table, labels[table], r) for r in rows] for table, rows in results.items()
    }
    data: dict[str, Any] = {
        "results": wire,
        "query": query,
        "searched": [t for t, _, _ in RECALL_SOURCES],
        "max_chars": max_chars,
        "_render": {"results": results, "sources": RECALL_SOURCES},
    }
    if not results:
        return O.nothing(
            "recall",
            f"Nothing recalled for {query!r}.\n"
            f"Searched: {', '.join(t for t, _, _ in RECALL_SOURCES)}.",
            **data,
        )
    return O.ok("recall", **data)


def similar(repo: Path, text: str, *, kinds: str = "", agent: str = "") -> O.Outcome:
    """ "Is this already filed?" -- the records most like ``text``, before it is added.

    Read-only: the same engine and the same ``[dedupe]`` knobs (``show_floor``,
    ``max_candidates``, ``kinds``) the add-time check uses, asked in the open. Candidates
    cross kinds -- a bug sees the open task that fixes it, a task the bug it would fix --
    and closed records stay in, because a new bug that repeats a fixed one is the case
    worth catching. Each says what it is, where it stands, how close it scored and which
    words it shares, so the match can be judged without opening it. Always runs, whatever
    ``[dedupe].on_match`` says: that setting governs what an ADD does, not whether one may
    look. ``kinds`` narrows to some of ``[dedupe].kinds``.
    """
    import dataclasses

    from ..services import similar as sim

    text = (text or "").strip()
    if not text:
        return O.failed(
            "similar",
            "nothing to compare: give the text of the record to be filed",
            candidates=[],
        )
    log, cfg, st = _load(repo, agent)
    allowed = list(cfg.dedupe.kinds)
    want = csv_list(kinds)
    unknown = [k for k in want if k not in allowed]
    if unknown:
        return O.failed(
            "similar",
            f"unknown kind {', '.join(unknown)}: [dedupe].kinds is {', '.join(allowed)}",
            candidates=[],
        )
    scope = want or allowed
    store = _store(repo, log, cfg)
    # The policy engine with the add-time switch forced on and the kinds narrowed.
    dd = dataclasses.replace(cfg.dedupe, on_match="ask", kinds=scope)
    scoped = dataclasses.replace(cfg, dedupe=dd)
    with sim.open_store(store) as matcher:
        found = sim.assess(matcher, {"kind": scope[0], "title": text, "body": ""}, scoped)
        # `assess` lists a record the text NAMES whatever its kind; `kinds` narrows those too.
        cands = [c for c in found.candidates if c.kind in scope]
        rows = DD.rows(st, matcher, cands, text)
    data = {
        "text": text,
        "kinds": scope,
        "show_floor": cfg.dedupe.show_floor,
        "candidates": rows,
        "count": len(rows),
    }
    if not rows:
        return O.nothing(
            "similar",
            f"Nothing in {', '.join(scope)} scores {cfg.dedupe.show_floor:g} or more "
            "against that text.",
            **data,
        )
    return O.ok("similar", **data)


def _sweep_records(st) -> list[dict[str, str]]:
    """Every record the pair sweep weighs, including the ones the ordinary index drops.

    A sweep is over the history that EXISTS, not only what is live: a removed item (filed
    and then taken back, frequently AS a duplicate -- B203 is one) and a superseded or
    forgotten record are exactly the pairs worth settling. `infra.store.similar_records`
    is the one definition of what a record's TEXT is, so this adds the dropped records to
    it rather than re-deriving titles and bodies.
    """
    from ..infra.store import similar_records

    out = similar_records(st)
    seen = {r["id"] for r in out}
    for it in st.items.values():
        if it.removed and it.id not in seen:
            out.append(
                {
                    "id": it.id,
                    "kind": it.kind,
                    "title": it.title,
                    "body": it.body,
                    "item": it.parent,
                }
            )
            seen.add(it.id)
    for m in st.memories.values():
        if not m.live and m.id not in seen:
            out.append({"id": m.id, "kind": "memory", "title": "", "body": m.text, "item": ""})
            seen.add(m.id)
    return out


def _is_open(st, rec: dict) -> bool:
    """Whether a swept record is still live: an OPEN item, an unresolved bug, an active
    lesson or decision, a live memory, any research. What `dupes --open-only` keeps."""
    from ..core.model import ABANDONED, DONE

    kind, rid = rec["kind"], rec["id"]
    if kind in ("task", "phase"):
        it = st.items.get(rid)
        return bool(it and not it.removed and it.state not in (DONE, ABANDONED))
    if kind == "bug":
        b = st.bugs.get(rid)
        return bool(b and b.open)
    if kind == "lesson":
        ls = st.lessons.get(rid)
        return bool(ls and not ls.superseded_by)
    if kind == "decision":
        d = st.decisions.get(rid)
        return bool(d and d.live)
    if kind == "memory":
        m = st.memories.get(rid)
        return bool(m and m.live)
    return kind == "research"


def _settled(st, a: str, b: str) -> bool:
    """Has this pair already been answered, in either direction? A recorded link
    (extends / duplicate_of / related), a `distinct` dismissal, or one lesson superseding
    the other. A pair answered once must never be offered again."""
    for x, y in ((a, b), (b, a)):
        lk = st.links.get(x)
        if lk is not None and (y in lk.linked() or y in lk.dismissed()):
            return True
        ls = st.lessons.get(x)
        if ls is not None and ls.superseded_by == y:
            return True
    return False


#: Fewer records than this can hold no pair.
_MIN_PAIR = 2


def pair_records(
    records: list[dict[str, str]],
    *,
    floor: float,
    settled: Any = None,
) -> list[dict[str, Any]]:
    """The pairs among ``records`` (id, kind, title, body) scoring at least ``floor``.

    The engine is `services.similar`'s exact TF-IDF cosine, asked of every record in
    turn; a canonical (a, b) key keeps each pair once, at its best score. Pure function
    of the records, so the sweep over a folded State and a sweep over a fixture are the
    SAME code -- which is how the acceptance set is checked without rebuilding a log.
    ``settled(a, b)`` asks whether the pair has already been answered.
    """
    from ..services import similar as sim

    if len(records) < _MIN_PAIR:
        return []
    index = sim.build(records)
    by_id = {r["id"]: r for r in records}
    answer = settled or (lambda _a, _b: False)
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for r in records:
        for other, score in index.query(r, limit=None):
            if other == r["id"] or score < floor or other not in by_id:
                continue
            if answer(r["id"], other):
                continue
            key = (r["id"], other) if r["id"] < other else (other, r["id"])
            if key in best:
                best[key]["score"] = max(best[key]["score"], score)
                continue
            best[key] = {"score": score, "a": key[0], "b": key[1]}
    rows = sorted(best.values(), key=lambda x: (-x["score"], x["a"], x["b"]))
    for row in rows:
        row["a_kind"] = by_id[row["a"]]["kind"]
        row["b_kind"] = by_id[row["b"]]["kind"]
        row["a_title"] = " ".join((by_id[row["a"]].get("title") or "").split())[:120]
        row["b_title"] = " ".join((by_id[row["b"]].get("title") or "").split())[:120]
    return rows


def pairs_from(
    st,
    cfg: Config,
    *,
    kinds: list[str] | None = None,
    open_only: bool = False,
    floor: float | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """The near-duplicate pairs in the log that nobody has settled.

    One implementation for `ddflow dupes` and the doctor note: gather the records a sweep
    weighs (`_sweep_records`), keep the live ones when asked, and pair them. Below the ask
    threshold the engine cannot tell a duplicate from a different-but-similar record
    (R-dedupe-matchers), so the floor defaults to ``[dedupe].show_floor`` -- a prompt to
    LOOK, which is what a sweep is -- and the reader decides with `ddflow link`.
    """
    fl = cfg.dedupe.show_floor if floor is None else float(floor)
    scope = list(kinds) if kinds else list(cfg.dedupe.kinds)
    records = [r for r in _sweep_records(st) if r["kind"] in scope]
    if open_only:
        records = [r for r in records if _is_open(st, r)]
    rows = pair_records(records, floor=fl, settled=lambda a, b: _settled(st, a, b))
    return rows[:limit] if limit else rows


def dupes(
    repo: Path,
    *,
    kinds: str = "",
    open_only: bool = False,
    floor: float | None = None,
    limit: int = 0,
    agent: str = "",
) -> O.Outcome:
    """ "Is anything filed twice?" -- the near-duplicate PAIRS already in the log.

    Where ``similar`` weighs one text about to be filed, this sweeps the records already
    held against each other. Pairs already answered (a recorded link, a `distinct`
    dismissal, one lesson superseding the other) are skipped, so a `distinct` verdict
    never returns. Read-only: settle a pair with ``ddflow link``.
    """
    _log, cfg, st = _load(repo, agent)
    allowed = list(cfg.dedupe.kinds)
    want = csv_list(kinds)
    unknown = [k for k in want if k not in allowed]
    if unknown:
        return O.failed(
            "dupes",
            f"unknown kind {', '.join(unknown)}: [dedupe].kinds is {', '.join(allowed)}",
            pairs=[],
        )
    scope = want or allowed
    shown = pairs_from(st, cfg, kinds=scope, open_only=open_only, floor=floor, limit=limit)
    fl = cfg.dedupe.show_floor if floor is None else float(floor)
    data: dict[str, Any] = {
        "pairs": shown,
        "count": len(shown),
        "kinds": scope,
        "open_only": bool(open_only),
        "floor": fl,
        "limit": limit,
    }
    if not shown:
        scope_word = "open " if open_only else ""
        return O.nothing(
            "dupes",
            f"No unsettled near-duplicate {scope_word}pair in {', '.join(scope)} scores "
            f"{fl:g} or more.",
            **data,
        )
    return O.ok("dupes", **data)


def link_record(
    repo: Path,
    subject: str,
    relation: str,
    target: str,
    *,
    reason: str = "",
    agent: str = "",
) -> O.Outcome:
    """Settle a pair: say how record ``subject`` relates to record ``target``.

    ``extends`` / ``duplicate_of`` / ``related`` record a link; ``distinct`` records a
    DISMISSAL ("I looked, these are different"), so the sweep never asks about the pair
    again. Two LESSONS answered ``extends`` / ``duplicate_of`` are MERGED the way B198
    asked: the target keeps both texts' tags and `seen_in`, and the duplicate is
    superseded by it -- the mechanism ``lesson add --supersedes`` uses, so there is one
    way a lesson is retired, not two. Nothing else is closed here: a duplicate BUG is
    closed only once its original is fixed (B-dup-closure).
    """
    relation = (relation or "").strip()
    if relation not in LINK_RELATIONS:
        return O.failed(
            "link.recorded",
            f"unknown relation {relation!r}: one of {', '.join(LINK_RELATIONS)}",
            subject=subject,
            target=target,
        )
    if not subject or not target:
        return O.refused(
            "link.recorded",
            "a link needs two record ids: `ddflow link <subject> --<relation> <target>`",
        )
    if subject == target:
        return O.refused(
            "link.recorded", "a record cannot link to itself", subject=subject, target=target
        )
    log, cfg, st = _load(repo, agent)
    skind, tkind = DD.kind_of(st, subject), DD.kind_of(st, target)
    for rid, kind in ((subject, skind), (target, tkind)):
        if not kind:
            return O.refused(
                "link.recorded",
                f"no record {rid!r} in this log: `ddflow recall` or `ddflow status` lists them",
                subject=subject,
                target=target,
            )
    merged: dict[str, str] = {}
    with log.transaction():
        log.append(
            "link.recorded",
            subject,
            {
                "relation": relation,
                "target": target,
                "by": cfg.agent.id,
                **({"reason": reason} if reason else {}),
            },
        )
        if relation in ("extends", "duplicate_of") and skind == tkind == "lesson":
            keep, dup = st.lessons[target], st.lessons[subject]
            log.append(
                "lesson.recorded",
                target,
                {
                    "supersedes": [subject],
                    "tags": list(dict.fromkeys([*keep.tags, *dup.tags])),
                    "seen_in": list(dict.fromkeys([*keep.seen_in, *dup.seen_in])),
                },
            )
            merged = {"superseded": subject, "by": target}
    return O.ok(
        "link.recorded",
        subject=subject,
        subject_kind=skind,
        relation=relation,
        target=target,
        target_kind=tkind,
        merged=merged,
    )


@dataclass
class Finding:
    """One research result, named once.

    Eleven fields that travel together — argparse flags, MCP input properties, event
    payload. The same reason `decisions.Draft` and `gates.Evidence` exist: a field added
    to one of those three lists is a field the other two silently drop.
    """

    question: str
    verdict: str
    claim: str = ""
    mechanism: str = ""
    falsifier: str = ""
    probe: str = ""
    probe_output: str = ""
    sources: str = ""
    budget: str = ""
    item: str = ""
    id: str = ""
    #: What the adder says about a possible duplicate (``_dedupe.Answer``).
    answer: DD.Answer | None = None


def research_add(repo: Path, finding: Finding, *, agent: str = "") -> O.Outcome:
    """Record a research finding, with the verdict it earned.

    Both refusals are the rule that makes the record worth keeping: a note with no
    verdict is a literature summary, and a CONFIRMED with no probe is an opinion wearing
    a label.
    """
    if finding.verdict not in VERDICTS:
        return O.failed(
            "research.recorded",
            "verdict must be CONFIRMED, REFUTED or THEORETICAL. A note with no verdict "
            "is a literature summary, not research.",
        )
    if finding.verdict in ("CONFIRMED", "REFUTED") and not (finding.probe or finding.probe_output):
        return O.failed(
            "research.recorded",
            f"{finding.verdict} requires a --probe (and ideally --probe-output): a "
            f"verdict with no probe behind it is an opinion. Use THEORETICAL and say why "
            f"no probe was possible.",
        )
    log, cfg, st = _load(repo, agent)
    rid = finding.id or auto_id("R", finding.question, finding.claim)
    taken = _research_id_taken(st, rid, finding)
    if taken is not None:
        return taken
    chk = DD.check_add(
        repo,
        log,
        cfg,
        st,
        DD.Record(
            kind="research",
            event_kind="research.recorded",
            rid=rid,
            title=finding.question,
            body="\n".join(x for x in (finding.claim, finding.mechanism) if x),
            item=finding.item,
        ),
        finding.answer,
    )
    if chk.refusal is not None:
        return chk.refusal
    if chk.extension:
        return DD.extend(log, cfg, chk, "research.recorded")
    with log.transaction():
        # Asked again under the log's lock: another agent may have filed this id (as
        # research or as anything else) since `st` was read. Only a NAMED id can collide
        # (`auto_id` is time-salted), so only an explicit --id pays for this re-read and
        # re-fold while the lock is held.
        if finding.id:
            raced = _research_id_taken(fold(log.read_all(), strict=False), rid, finding)
            if raced is not None:
                return raced
        log.append(
            "research.recorded",
            rid,
            _research_fields(finding) | chk.fields,
        )
        DD.after_add(log, cfg, rid, chk)
    return O.ok("research.recorded", id=rid, verdict=finding.verdict, **chk.data())


def _research_fields(finding: Finding) -> dict[str, Any]:
    """What a research note records, as the fold will hold it. These keys are both the
    event payload (with the dedupe fields beside them) and what "the same record again"
    compares (`_research_id_taken`), so a field added here is compared too."""
    return {
        "question": finding.question,
        "claim": finding.claim,
        "mechanism": finding.mechanism,
        "falsifier": finding.falsifier,
        "probe": finding.probe,
        "probe_output": finding.probe_output,
        "verdict": finding.verdict,
        "sources": csv_list(finding.sources),
        "budget": finding.budget,
        "item": finding.item,
    }


def _research_id_taken(st, rid: str, finding: Finding) -> O.Outcome | None:
    """A re-add under an id that is already filed (B14d796e03b), or None to go ahead.

    The add-time check passes a taken id straight through, and the fold replaces a note
    wholesale, so a second `research add --id R1` used to overwrite R1's question, claim
    and verdict with no trace in state. The same record again is idempotent (nothing is
    written); anything else is refused and pointed at the paths that keep both."""
    kind = DD.kind_of(st, rid)
    if not kind:
        return None
    if kind != "research":
        return O.refused(
            "research.recorded",
            f"{rid} is already a {kind}: a research note needs an id of its own (choose "
            f"another --id, or omit it for a generated one).",
            id=rid,
        )
    old = st.research[rid]
    new = _research_fields(finding)
    if all(getattr(old, k) == v for k, v in new.items()):
        return O.ok("research.recorded", id=rid, verdict=finding.verdict, unchanged=True)
    differ = [k for k, v in new.items() if getattr(old, k) != v]
    return O.refused(
        "research.recorded",
        f"research {rid} already exists ({old.verdict.lower() or 'recorded'}: "
        f"{old.question[:100]}), and this add differs in {', '.join(differ)}. Adding never "
        f"overwrites a record. To add to it: --extends {rid} without --id. To file a "
        f"separate finding: another --id, or none for a generated one.",
        id=rid,
        differs=differ,
    )


BUG_SCOPES = ("project", "ddflow")
BUG_SEVERITIES = ("low", "medium", "high", "critical")
#: The command line (with `{id}`) that prepares an upstream report, once there is one ("" until).
#: The offer to prepare a report names it, so it is only made when this is set
#: (tests/test_bug_scope.py pins it to the parser).
BUG_REPORT_COMMAND = ""


def upstream_offer(scope: str, bid: str) -> str:
    """One line offering an upstream report for a ddflow-scoped bug; '' when the bug is
    the project's own or the command that prepares the report is not there yet."""
    if scope != "ddflow" or not BUG_REPORT_COMMAND:
        return ""
    return (
        f"This bug is in ddflow itself: `{BUG_REPORT_COMMAND.format(id=bid)}` "
        "prepares an upstream report."
    )


def bug_found(  # noqa: PLR0913 -- BACKLOG B179: a BugDraft record, as task_add's TaskDraft
    repo: Path,
    *,
    summary: str,
    item: str = "",
    id: str = "",
    title: str = "",
    severity: str = "",
    scope: str = "",
    globs: str = "",
    no_task: bool = False,
    answer: DD.Answer | None = None,
    agent: str = "",
) -> O.Outcome:
    """File a bug. One that reads like an existing record is refused until ``answer``
    says what it is; a bug a task will fix is filed against it (``item``), which answers
    that candidate. ``answer`` extending an OPEN bug appends to it and files nothing.
    ``title``, ``severity`` (low|medium|high|critical) and ``scope`` (``project``, or
    ``ddflow`` for a bug in ddflow itself) are optional event fields.

    A bug is an item in the queue (B-bugs-as-items): unless ``no_task`` (a bug fixed in
    the commit that found it) or ``[bugs].file_task`` is off, the same transaction files
    its fix task -- `_file_fix_task` -- and the reply carries ``fix_task``. ``globs`` are
    the fix task's files; absent, the item's. An OPEN bug-fix task named as ``item`` is
    the fix itself and nothing new is filed."""
    scope = (scope or "").strip().lower()
    severity = (severity or "").strip().lower()
    title = " ".join((title or "").split())
    bad = _bug_fields_problem(scope, severity, globs)
    if bad:
        return O.failed("bug.found", bad)
    log, cfg, st = _load(repo, agent)
    bid = id or auto_id("B", summary, item)
    chk = DD.check_add(
        repo,
        log,
        cfg,
        st,
        DD.Record(
            kind="bug", event_kind="bug.found", rid=bid, title=title, body=summary, item=item
        ),
        answer,
    )
    if chk.refusal is not None:
        return chk.refusal
    # Only what was said is written, so a plain re-report of a ddflow-scoped bug (same
    # id) does not turn it back to `project`; `--scope project` says it and does.
    extra = {k: v for k, v in (("title", title), ("severity", severity), ("scope", scope)) if v}
    if chk.extension:
        # The text goes onto the OPEN bug it was answered onto; what was said about its
        # title, severity or scope goes with it (a merge: an empty field never blanks one).
        target = chk.extension["target"]
        held = st.bugs.get(target)  # the target may be another kind of record
        with log.transaction():
            if extra and held is not None:
                log.append("bug.found", target, dict(extra))
            out = DD.extend(log, cfg, chk, "bug.found")
        offer = upstream_offer(scope or (held.scope if held else "project"), target)
        return O.ok("bug.found", **{**out.data, **({"offer": offer} if offer else {})})
    prior = st.bugs.get(bid)
    # The bug's own event names its fix task, and is written FIRST: the lock is no
    # rollback (each append is durable on its own), so the order decides what a crash
    # between the two leaves behind. A bug naming a task not yet filed is repaired by
    # `bug file-tasks`, which walks open bugs whose task is missing as well as those with
    # none, and by a re-report of the same id, which files the task the record names and
    # has not got; a task for a bug that was never recorded would be repaired by nothing.
    # Any other re-report (`prior`) files nothing: the record already has what it has.
    # What the reply then SAYS about the task -- claim it now, it is queued, ask -- is the
    # `[bugs].on_found` knob's business (B-bugs-fix-now), decided from `fix_task` here.
    fix: dict[str, Any] = {"fix_task": "", "filed": False}
    with log.transaction():
        # Decided from the log as it is NOW, under the lock: a task another process filed
        # (or claimed) since `_load` must be seen, or it is filed twice -- a second
        # definition of a live id, over its holder.
        st = fold(log.read_all(), strict=False)
        prior = st.bugs.get(bid)
        dangling = prior is not None and prior.open and bool(prior.fix_task)
        dangling = dangling and not _live(st, prior.fix_task)  # type: ignore[union-attr]
        filing = cfg.bugs.file_task and not no_task and (prior is None or dangling)
        fix_id = _fix_task_id(st, cfg, bid, item) if filing else ""
        linked = {"fix_task": fix_id} if fix_id else {}
        log.append(
            "bug.found",
            bid,
            {"item": item, "summary": summary, **extra, **linked, **chk.fields},
        )
        DD.after_add(log, cfg, bid, chk)
        if filing:
            fix = _file_fix_task(
                log, cfg, st, bid, title=title, summary=summary, item=item, globs=globs
            )
    # A re-report merges into the record and never reopens it (see `_h_bug_found`). Said
    # out loud, because otherwise a real recurrence filed under an id already closed
    # vanishes without a word. Only an EXPLICIT `--id` can land on a closed record: an
    # auto id is time-salted (core/ids.py), so the same text without an id is a new id,
    # and the duplicate check above is what catches it.
    offer = upstream_offer(scope or (prior.scope if prior else "project"), bid)
    more: dict[str, Any] = {"offer": offer} if offer else {}
    more["fix_task"] = fix["fix_task"] or (prior.fix_task if prior else "")
    more["fix_task_filed"] = fix["filed"]
    if prior is not None and prior.resolution:
        return O.ok("bug.found", id=bid, resolution=prior.resolution, **more, **chk.data())
    return O.ok("bug.found", id=bid, **more, **chk.data())


#: Fix tasks are filed as `fix-<bug id>`: one obvious name per bug, so a second report
#: of the same id finds the task already there instead of filing a twin.
FIX_TASK_PREFIX = "fix-"
#: A fix task's title is the bug's headline behind "Fix bug X:" -- the words `show <bug>`
#: already recognises as a fix's own claim (`api.reporting._show_bug`). Cut, not wrapped:
#: the whole summary is in the body.
_FIX_TITLE_MAX = 120


def _fix_task_of(st, cfg, item: str):
    """The OPEN bug-fix task ``item`` names, or None. A report filed against the task that
    is fixing it (`bug found --item <fix task>`, the dedupe's `filed_against`) names its
    fix; one filed against the item it was FOUND in -- a feature, a finished task, a
    phase -- names where to look, and gets a task of its own."""
    from ..core.flow import FEATURE, branch_kind
    from ..core.model import ABANDONED, DONE

    it = st.items.get(item) if item else None
    if it is None or it.removed or it.kind != "task" or it.state in (DONE, ABANDONED):
        return None
    return it if branch_kind(it, cfg) != FEATURE else None


def _open_phase_of(st, item: str) -> str:
    """The nearest OPEN phase at or above ``item``, or "". A finished phase does not take
    new work: a task filed under it would sit open beneath a phase that says done."""
    from ..core.model import ABANDONED, DONE

    it = st.items.get(item) if item else None
    if it is None or it.removed:
        return ""
    for node in (it, *st.ancestors(item)):
        if node.kind == "phase" and node.state not in (DONE, ABANDONED) and not node.removed:
            return node.id
    return ""


def _own_fix_id(st, bug_id: str) -> str:
    """The bug's own fix task id: `fix-<bug>`, or -- when that one was ABANDONED, which
    nothing revives -- the first of `fix-<bug>-2`, `-3`, ... that is not abandoned too
    (B974e34fa83). Decided from the fold the caller holds inside its transaction, so the
    id is free when it is filed (L-free-id-before-add)."""
    from ..core.model import ABANDONED

    base = tid = FIX_TASK_PREFIX + bug_id
    n = 1
    while (it := st.items.get(tid)) is not None and not it.removed and it.state == ABANDONED:
        n += 1
        tid = f"{base}-{n}"
    return tid


def _fix_task_id(st, cfg, bug_id: str, item: str) -> str:
    """The id `_file_fix_task` will bind ``bug_id`` to: the open bug-fix task ``item``
    names, else its own fix task (`_own_fix_id`, whether or not it is already in the
    queue). One rule, so the bug event written before the task names the task that then
    gets filed."""
    named = _fix_task_of(st, cfg, item)
    return named.id if named is not None else _own_fix_id(st, bug_id)


def _has_fix_task(st, cfg, bug_id: str, item: str) -> bool:
    """Whether ``bug_id`` already has its fix in the queue, so `_file_fix_task` would file
    nothing: the open bug-fix task ``item`` names, or its own fix task, not abandoned."""
    if _fix_task_of(st, cfg, item) is not None:
        return True
    have = st.items.get(_own_fix_id(st, bug_id))
    return have is not None and not have.removed


def _file_fix_task(
    log, cfg, st, bug_id: str, *, title: str, summary: str, item: str, globs: str
) -> dict[str, Any]:
    """File the task that fixes ``bug_id`` and return ``{fix_task, filed, phase_made}``.

    The task: `fix-<bug>`, tagged a bug fix (the first of `[flow] bugfix_tags`, so
    `bugs_first` and gitflow both see it), under the item's open phase -- else the standing
    `[bugs] phase`, made on first use -- carrying ``globs`` or the item's, the item's
    priority and release line, and `fixes = [bug]`. Appends inside the caller's
    transaction; ``st`` is updated in place so a loop (`bug_file_tasks`) sees what it made.
    ``filed`` is False when the bug already has its fix: the open bug-fix task ``item``
    names, or a `fix-<bug>` already in the queue.
    """
    from ..api.items import DEFAULT_PRIORITY
    from ..core.model import Item

    tid = _fix_task_id(st, cfg, bug_id, item)
    if _has_fix_task(st, cfg, bug_id, item):
        return {"fix_task": tid, "filed": False, "phase_made": ""}
    # A removed source still lends its files, priority and line: the bug is in those files
    # whether or not the item that touched them is still in the queue, and the fix task's
    # globs are what makes a feature on them wait. Only the parent needs an OPEN phase.
    src = st.items.get(item) if item else None
    parent = _open_phase_of(st, item)
    phase_made = ""
    if not parent:
        standing = st.items.get(cfg.bugs.phase)
        if standing is None or standing.removed:
            phase_made = cfg.bugs.phase
            log.append(
                "phase.added",
                phase_made,
                {
                    "title": "Bugs",
                    "needs": [],
                    "globs": [],
                    "body": "Fix tasks for bugs found outside any open phase (`bug found`).",
                    "tags": [],
                    "priority": DEFAULT_PRIORITY,
                    "line": "",
                },
            )
            st.items[phase_made] = Item(id=phase_made, kind="phase", title="Bugs")
            parent = phase_made
        else:
            parent = _open_phase_of(st, standing.id)  # "" when the standing phase is done
    tags = list(cfg.flow.bugfix_tags)
    tag = "bugfix" if "bugfix" in tags else (tags[0] if tags else "bugfix")
    first = summary.strip().splitlines()[0] if summary.strip() else ""
    headline = " ".join((title or first).split())
    full_title = f"Fix bug {bug_id}: {headline}" if headline else f"Fix bug {bug_id}"
    if len(full_title) > _FIX_TITLE_MAX:
        full_title = full_title[: _FIX_TITLE_MAX - 3].rstrip() + "..."
    where = f" Found on {item}." if item else ""
    body = (
        f"Fixes bug {bug_id}: {summary.strip()}{where}\n\n"
        f"Write the regression test first and watch it FAIL on the unfixed code; then "
        f"`ddflow complete {tid} --regression-test <test>` closes the bug with the task "
        f"(or `ddflow bug fixed {bug_id} --regression-test <test>` first)."
    )
    data = {
        "parent": parent,
        "title": full_title,
        "needs": [],
        "globs": GS.parse(globs) if globs else list(src.globs if src else []),
        "body": body,
        "tags": [tag],
        "priority": src.priority if src else DEFAULT_PRIORITY,
        "line": src.line if src else "",
        "fixes": [bug_id],
    }
    log.append("task.added", tid, data)
    st.items[tid] = Item(id=tid, kind="task", title=full_title, parent=parent, fixes=[bug_id])
    return {"fix_task": tid, "filed": True, "phase_made": phase_made}


def _live(st, item: str) -> bool:
    """Whether ``item`` names an item in the queue (recorded and not removed)."""
    it = st.items.get(item) if item else None
    return it is not None and not it.removed


def _needs_fix_task(st, b) -> bool:
    """Whether open bug ``b`` has no fix task that will ever fix it: none in the queue; a
    DONE task it was merely reported against -- `bug found --item <open fix task>` links
    a report to that task, and the task's completion does not fix it (B8dcbf2f8da); or an
    ABANDONED task, which nothing sends back -- its own `fix-<bug>` included, refiled as
    `fix-<bug>-2` (`_own_fix_id`, B974e34fa83). Left alone: a done task's OWN bug
    (`verify --reopen` sends that task back)."""
    from ..core.model import ABANDONED, DONE
    from ..services.completion import fixes_of

    if not _live(st, b.fix_task):
        return True
    state = st.items[b.fix_task].state
    if state == ABANDONED:
        return True
    return state == DONE and b.id not in fixes_of(st, b.fix_task)


def bug_file_tasks(repo: Path, *, dry_run: bool = False, agent: str = "") -> O.Outcome:
    """Give every OPEN bug that has no fix task one -- the one-shot upgrade for a log
    written before `bug found` filed them, and the repair for a bug whose `fix_task` names
    an item that is not in the queue (a crash between the two appends of `bug found`) or a
    finished task it was only reported against (`_needs_fix_task`, B8dcbf2f8da). A
    bug whose item is an open bug-fix task is linked to it (``linked``); any other gets
    `fix-<bug>` filed (``filed``), as `bug found` would have. Nothing to do is exit 2.
    ``dry_run`` reports and writes nothing."""
    log, cfg, st = _load(repo, agent)
    filed: list[str] = []
    linked: list[str] = []
    tasks: dict[str, str] = {}
    #: {bug: {task, state}} for the linked ones: the task may be finished (B70d80555a4).
    links: dict[str, dict[str, str]] = {}
    with log.transaction():
        st = fold(log.read_all(), strict=False)
        todo = sorted(
            (b for b in st.bugs.values() if b.open and _needs_fix_task(st, b)),
            key=lambda b: (b.found_at, b.id),
        )
        for b in todo:
            if dry_run:
                # The same test the write path applies, so the prediction is the outcome.
                if _has_fix_task(st, cfg, b.id, b.item):
                    linked.append(b.id)
                    links[b.id] = _link(st, _fix_task_id(st, cfg, b.id, b.item))
                else:
                    filed.append(b.id)
                    tasks[b.id] = _fix_task_id(st, cfg, b.id, b.item)
                continue
            fix = _file_fix_task(
                log, cfg, st, b.id, title=b.title, summary=b.summary, item=b.item, globs=""
            )
            # A partial `bug.found` carries the link: the fold merges and never blanks
            # (`_h_bug_found`), and an older ddflow folds it as the record it already has.
            log.append("bug.found", b.id, {"fix_task": fix["fix_task"]})
            (filed if fix["filed"] else linked).append(b.id)
            if not fix["filed"]:
                links[b.id] = _link(st, fix["fix_task"])
            if fix["filed"]:
                # The id filed, not `fix-<bug>` assumed: an abandoned one gets a successor.
                tasks[b.id] = fix["fix_task"]
    if not filed and not linked:
        return O.nothing(
            "bug.file_tasks",
            "every open bug already has a fix task",
            filed=[],
            linked=[],
            tasks={},
            links={},
            dry_run=dry_run,
        )
    return O.ok(
        "bug.file_tasks", filed=filed, linked=linked, tasks=tasks, links=links, dry_run=dry_run
    )


def _link(st, task: str) -> dict[str, str]:
    """The task a bug was linked to, with its state (open, running, done, ...); "missing"
    should it not be in the queue, which a link is only made to when it is."""
    it = st.items.get(task)
    return {"task": task, "state": it.state if it is not None and not it.removed else "missing"}


def _bug_fields_problem(scope: str, severity: str, globs: str) -> str:
    """Why `bug found`'s optional fields cannot be recorded, or ""."""
    if scope and scope not in BUG_SCOPES:
        return f"unknown scope {scope!r}: one of {', '.join(BUG_SCOPES)}"
    if severity and severity not in BUG_SEVERITIES:
        return f"unknown severity {severity!r}: one of {', '.join(BUG_SEVERITIES)}"
    return GS.problem(GS.parse(globs))


def _unknown_bug(kind: str, bid: str, st) -> O.Outcome:
    """The refusal for an id that names no recorded bug, shared by every closure.

    An unknown id used to be closed anyway, folding a phantom bug while the real one
    stayed open -- typically the TASK id, passed because `bug found --item` links one.
    """
    linked = sorted(b.id for b in st.bugs.values() if b.item == bid and b.open)
    hint = (
        f" Open bugs linked to {bid}: {', '.join(linked)} -- close those ids."
        if linked
        else " `ddflow recall` or `ddflow status` lists the open bugs."
    )
    return O.refused(kind, f"no bug {bid} is recorded in this log.{hint}", id=bid)


def bug_fixed(
    repo: Path,
    item: str,
    *,
    regression_test: str | list[str] = "",
    lesson: str = "",
    lesson_title: str = "",
    lesson_rule: str = "",
    agent: str = "",
    changelog: str = "",
) -> O.Outcome:
    """Close a bug. Refuses without the test that would catch it again.

    `regression_test` is one test or several: a list (a repeated CLI flag, an MCP
    array), each entry itself split on ',' and ';' outside a parametrize id's brackets
    (B227585c781). The event keeps `regression_test` as the string every reader already
    displays -- as given (stripped) when one string was given, ', '-joined from a list --
    and `regression_tests` as the split list. The required-test rule asks the LIST: `;`
    alone is a truthy string naming no test.
    """
    entry: dict[str, Any] = {}
    if changelog:
        try:
            entry = parse_changelog(changelog)
        except ValueError as e:
            return O.failed("bug.fixed", str(e), id=item)
    log, cfg, st = _load(repo, agent)
    parts = [regression_test] if isinstance(regression_test, str) else list(regression_test)
    tests = [t for part in parts for t in _split_outside_brackets(str(part or ""))]
    regression_test = (
        regression_test.strip() if isinstance(regression_test, str) else ", ".join(tests)
    )
    if not tests and cfg.lessons.require_regression_test:
        return O.failed(
            "bug.fixed",
            "a bug may not be closed without --regression-test naming the test that "
            "would catch it again. Write the test, watch it FAIL against the unfixed "
            "code, then close.",
            id=item,
        )
    if item not in st.bugs:
        return _unknown_bug("bug.fixed", item, st)
    missing, unchecked = _unresolved_tests(repo, regression_test)
    if missing:
        joined = [m for m in missing if _looks_like_several(m)]
        hint = (
            f" {'; '.join(repr(m) for m in joined)} looks like several tests in one entry: "
            f"separate them with ',' or ';', or repeat --regression-test."
            if joined
            else ""
        )
        return O.failed(
            "bug.fixed",
            f"--regression-test: {len(missing)} of {len(tests)} test(s) exist in no "
            f"worktree of this repository: {'; '.join(missing)}.{hint} Name the tests "
            f"that now guard this bug.",
            id=item,
        )
    log.append(
        "bug.fixed",
        item,
        {
            "regression_test": regression_test,
            "regression_tests": tests,
            "lesson": lesson,
            **({"changelog": entry} if entry else {}),
        },
    )
    captured = ""
    if cfg.lessons.auto_capture_on_bug and lesson_title:
        captured = f"L-{item}"
        log.append(
            "lesson.recorded",
            captured,
            {
                "title": lesson_title,
                "rule": lesson_rule,
                "seen_in": [item],
                "tags": ["bug"],
            },
        )
    return O.ok(
        "bug.fixed",
        id=item,
        regression_test=regression_test,
        regression_tests=tests,
        lesson_captured=captured,
        unchecked=unchecked,
    )


def bug_invalid(
    repo: Path, bug: str, *, reason: str, evidence: str = "", agent: str = ""
) -> O.Outcome:
    """Close a bug as a FALSE finding: nothing was broken, so nothing was fixed.

    `bug fixed` was the only closure, and it claims a repair plus a regression test that
    fails on the unfixed code. A finding shown false has neither, so B97355c6d15 stayed
    open forever -- and closing it as fixed would have recorded a repair nobody made. This
    closure never sets `fixed_at` and never counts as a fix.

    Refused (exit 3): an unknown id, a bug already closed either way (a fixed bug is not
    re-labelled false after the fact), and an empty reason -- "invalid" with no why is an
    unexplained dismissal. `evidence` is the probe that showed it false: a command, or a
    test node id, which is resolved statically as `--regression-test` is (exit 1 when it
    names nothing).
    """
    log, _cfg, st = _load(repo, agent)
    if not reason.strip():
        return O.refused(
            "bug.invalid",
            "a bug may not be closed as invalid without --reason saying why the finding "
            "is false; pass --evidence with the probe or test that showed it.",
            id=bug,
        )
    if bug not in st.bugs:
        return _unknown_bug("bug.invalid", bug, st)
    rec = st.bugs[bug]
    if rec.resolution == "fixed":
        return O.refused(
            "bug.invalid",
            f"bug {bug} is already closed as fixed (regression test: "
            f"{rec.regression_test or 'none recorded'}); a fixed bug is not re-labelled "
            f"a false finding.",
            id=bug,
        )
    if rec.resolution == "invalid":
        return O.refused(
            "bug.invalid",
            f"bug {bug} is already closed as invalid: {rec.invalid_reason}",
            id=bug,
        )
    missing, unchecked = _unresolved_tests(repo, evidence)
    if missing:
        return O.failed(
            "bug.invalid",
            f"--evidence names a test that exists in no worktree of this repository: "
            f"{', '.join(missing)}. Name the test or probe that shows the finding false.",
            id=bug,
        )
    reason = reason.strip()
    with log.transaction():
        log.append("bug.invalid", bug, {"reason": reason, "evidence": evidence})
        # Decided from the log as it is NOW, under the lock: a claim on the fix task made
        # since `_load` must be seen, or the task is removed under its holder.
        fresh = fold(log.read_all(), strict=False)
        rec = fresh.bugs.get(bug, rec)  # the record the removal is decided from, and reported
        removed, kept = _drop_fix_task(log, fresh, rec)
    return O.ok(
        "bug.invalid",
        id=bug,
        invalid_reason=reason,
        evidence=evidence,
        unchecked=unchecked,
        fix_task=rec.fix_task,
        fix_task_removed=removed,
        fix_task_kept=kept,
    )


#: Why `bug invalid` left a fix task alone (`fix_task_kept`), with the words the surfaces
#: say: the queue still wants it (`STILL_QUEUED`), or there is nothing to remove. "" when
#: the bug has no fix task or it was removed. ONE vocabulary, read by the CLI, so a
#: reason added here cannot fall through to the wrong sentence there (roborev, job 1300).
STILL_QUEUED: dict[str, str] = {
    "held": "somebody holds it",
    "needed": "an item is filed under it or needs it",
    "shared": "it fixes another open bug too",
}
NOTHING_TO_REMOVE: dict[str, str] = {
    "finished": "already finished",
    "removed": "already removed from the queue",
    "missing": "not in the queue at all",
}


def _drop_fix_task(log, st, rec) -> tuple[str, str]:
    removed, why = _drop_fix_task_unchecked(log, st, rec)
    assert not why or why in STILL_QUEUED or why in NOTHING_TO_REMOVE, why
    return removed, why


def _drop_fix_task_unchecked(log, st, rec) -> tuple[str, str]:
    """Take a false finding's fix task out of the queue, when nothing else wants it: it is
    still OPEN, nobody holds it, no other open bug names it, nothing is filed under it and
    nothing `needs` it -- the guards `api.remove` applies, so a removal here strands no
    one. Returns ``(removed id, "")`` or ``("", why kept)``, the reason a key of
    `STILL_QUEUED` or `NOTHING_TO_REMOVE` (asserted, so a new reason cannot reach the
    surfaces without its sentence),
    so the reply can say what is true: a task somebody has claimed, or that fixes a real
    bug too, stays and the agent decides; a finished or removed one is not "still queued"."""
    from ..core.model import OPEN

    if not rec.fix_task:
        return "", ""
    t = st.items.get(rec.fix_task)
    if t is None:
        return "", "missing"
    if t.removed:
        return "", "removed"
    if t.lease:  # before the state: a claimed task is RUNNING, and held is the point
        return "", "held"
    if t.state != OPEN:
        return "", "finished"
    if any(b.open and b.id != rec.id and b.fix_task == t.id for b in st.bugs.values()):
        return "", "shared"
    if st.open_descendants(t.id) or any(
        not o.removed and t.id in o.needs for o in st.items.values()
    ):
        return "", "needed"
    log.append("task.removed", t.id, {"reason": f"bug {rec.id} closed as invalid"})
    return t.id, ""


def _unresolved_tests(repo: Path, spec: str) -> tuple[list[str], list[str]]:
    """Split a `--regression-test` list into (missing, unchecked) pytest node ids.

    A node id (`path.py::name[...]`, `path.py::Class::name`) is looked up in EVERY
    worktree, because a bug is closed from the fix branch before its test reaches the
    base. Only names are checked, statically: whether the test FAILS without the fix is
    B-bugfix-verified's job. Anything that is not a Python node id -- a spec, a shell
    command -- cannot be resolved here and is returned as unchecked, not refused.
    """
    from ..infra import worktree as W

    trees = [Path(t["worktree"]) for t in W.list_worktrees(repo) if t.get("worktree")] or [repo]
    missing: list[str] = []
    unchecked: list[str] = []
    for entry in _split_outside_brackets(spec):
        path, sep, names = entry.partition("::")
        # A command (`pytest tests/test_x.py`) can end in `.py` too; a path has no
        # whitespace (B65bbe327c7).
        if not path.endswith(".py") or any(c.isspace() for c in path):
            unchecked.append(entry)
            continue
        # The parametrize id is cut off BEFORE splitting: `::` and `,` are legal inside
        # `[...]`, and splitting them refused a real test (B-bfu-param-sep).
        wanted = names.split("[", 1)[0].split("::") if sep else []
        # `path::` or `path::[p]` names no test; an empty part must not pass for one.
        # Several tests joined by whitespace are never one test: `a.py::t[1] a.py::t2`
        # resolved `t` and accepted the unchecked rest (B227585c781).
        if (
            "" in wanted
            or _looks_like_several(entry)
            or not any(_defines(_inside(tree, path), wanted) for tree in trees)
        ):
            missing.append(entry)
    return missing, unchecked


def _looks_like_several(entry: str) -> bool:
    """A node id followed, after whitespace OUTSIDE its `[...]`, by another test path:
    tests joined by spaces, not one test (`a.py::t1 a.py::t2`, `a.py::t[1] a.py::t2`).

    Asked of a node id whose path has no whitespace (a command never gets here). Inside
    the brackets anything goes -- a parameter id may hold spaces, `::` and `.py`
    (`t[python foo.py -v]`, `t[a b::c]`) -- and a value may hold `]` itself (`t[x] y]`),
    so only a following token that starts like a test PATH counts. A bare trailing word
    is not refused: the permissive side, since refusing a real test locks the bug open.
    """
    depth, tokens, cur = 0, [], []
    for ch in entry:
        if ch.isspace() and depth == 0:
            tokens.append("".join(cur))
            cur = []
            continue
        depth = max(0, depth + {"[": 1, "]": -1}.get(ch, 0))
        cur.append(ch)
    tokens.append("".join(cur))
    return any(tok.split("::", 1)[0].endswith(".py") for tok in tokens[1:] if tok)


def _split_outside_brackets(spec: str) -> list[str]:
    """Entries separated by ',' or ';', ignoring both inside a parametrize id's brackets.

    ';' as well as ',' (B227585c781): a ';'-joined list was resolved as one node id and
    refused as a single missing test.
    """
    out, depth, cur = [], 0, []
    for ch in spec:
        if ch in ",;" and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        depth += {"[": 1, "]": -1}.get(ch, 0)
        cur.append(ch)
    out.append("".join(cur))
    return [e.strip() for e in out if e.strip()]


def _inside(tree: Path, path: str) -> Path | None:
    """`tree / path`, or None when it resolves outside `tree`.

    An absolute path made `tree / path` discard the tree, and `..` walks out of it, so
    any file anywhere could close a bug (B-bfu-abs-path).
    """
    candidate = (tree / path).resolve()
    return candidate if candidate.is_relative_to(tree.resolve()) else None


def _defines(source: Path | None, names: list[str]) -> bool:
    """Does `source` exist and define the node `names` -- module, then class, then method?

    Resolved as a CHAIN: matching each name anywhere in the file accepted a method for a
    module-level test and a function outside the class for `Class::method`
    (B-bfu-structure). A file that does not parse under THIS interpreter may still be
    valid under the project's own, so it falls back to finding each name defined
    somewhere -- the permissive side, since refusing a real test locks the bug open.
    """
    if source is None:
        return False
    try:
        raw = source.read_bytes()
    except OSError:
        return False
    try:
        # Bytes, so a PEP 263 coding cookie is honoured as pytest would (B1d4b2e6914).
        scope: list[ast.stmt] = ast.parse(raw).body
    except (SyntaxError, ValueError):
        text = raw.decode("utf-8", errors="replace")
        return all(
            re.search(rf"^\s*(?:async\s+def|def|class)\s+{re.escape(n)}\b", text, re.M)
            for n in names
        )
    module = _definitions(scope)
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for depth, name in enumerate(names):
        found = (
            next((d for d in module if d.name == name), None)
            if node is None
            else _member(node, name, module, set())
        )
        if found is None:
            return False
        if found is True:
            return True
        node = found
        if depth < len(names) - 1 and not isinstance(node, ast.ClassDef):
            return False  # pytest collects from classes only, never a function body
    return True


def _member(
    cls: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    module: list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef],
    seen: set[str],
) -> ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | bool | None:
    """`name` defined in `cls` or, as pytest collects it, inherited from a base.

    Bases are followed when they are classes of the same module (Bd671110650). A base
    defined elsewhere cannot be read here, so its members are unknown: True, the
    permissive side, since refusing a real test locks the bug open.
    """
    own = next((d for d in _definitions(cls.body) if d.name == name), None)
    if own is not None or not isinstance(cls, ast.ClassDef):
        return own
    seen.add(cls.name)
    unknown = False
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id == "object":
            continue  # defines no tests; treating it as unknown would accept any name
        local = next(
            (
                d
                for d in module
                if isinstance(base, ast.Name) and isinstance(d, ast.ClassDef) and d.name == base.id
            ),
            None,
        )
        if local is None:
            unknown = True
        elif local.name not in seen:
            hit = _member(local, name, module, seen)
            if hit is not None:
                return hit
    return True if unknown else None


def _definitions(
    body: list[ast.stmt],
) -> list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]:
    """Classes and functions defined directly in `body`, including under `if`/`try`/`with`
    at the same level, but not inside another definition."""
    out: list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            out.append(node)
        elif isinstance(
            node, ast.If | ast.Try | ast.With | ast.AsyncWith | ast.For | ast.AsyncFor | ast.While
        ):
            for block in (node.body, getattr(node, "orelse", []), getattr(node, "finalbody", [])):
                out += _definitions(block)
            for handler in getattr(node, "handlers", []):
                out += _definitions(handler.body)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                out += _definitions(case.body)
    return out


def session_start(repo: Path, *, model: str = "", tool: str = "", agent: str = "") -> O.Outcome:
    from ..services import sessions as S

    log, cfg, _st = _load(repo, agent)
    return O.ok("session.started", session=S.start(log, cfg, model=model, agent_tool=tool))


#: Why an empty session record is refused rather than written.
_EMPTY_SESSION_TEXT = (
    "refusing an empty {what}: the text is empty or whitespace-only, and nothing was "
    "recorded. Pass the words with --text (or pipe them on stdin)."
)


def session_prompt(
    repo: Path, session: str, text: str, *, item: str = "", agent: str = ""
) -> O.Outcome:
    """Record the operator's own words, with credentials redacted before they touch disk.

    Empty or whitespace-only text is refused, recording nothing: an empty prompt in the
    log is a hole `ddflow replay` cannot see as one. A missing session id is not a
    reason to refuse: the latest open session is used, else an implicit one is opened,
    and `how` says which.
    """
    from ..services import sessions as S

    if not (text or "").strip():
        return O.failed(
            "session.prompt", _EMPTY_SESSION_TEXT.format(what="prompt"), session=session
        )
    log, cfg, _st = _load(repo, agent)
    if not cfg.session.log_prompts:
        # Nothing is recorded, so no session is opened for it either.
        return O.ok("session.prompt", redactions=0, session=session, how="off")
    sid, how = S.resolve(log, session)
    return O.ok(
        "session.prompt", redactions=S.prompt(log, cfg, sid, text, item=item), session=sid, how=how
    )


def session_note(
    repo: Path, session: str, text: str, *, item: str = "", agent: str = ""
) -> O.Outcome:
    from ..services import sessions as S

    if not (text or "").strip():
        return O.failed("session.note", _EMPTY_SESSION_TEXT.format(what="note"), session=session)
    log, cfg, _st = _load(repo, agent)
    sid, how = S.resolve(log, session)
    S.note(log, cfg, sid, text, item=item)
    return O.ok("session.note", session=sid, how=how)


def session_adopt_orphans(repo: Path, *, agent: str = "") -> O.Outcome:
    """Attach prompts and notes recorded with no session id to the nearest session."""
    from ..services import sessions as S

    log, _cfg, _st = _load(repo, agent)
    return O.ok("session.adopted", adopted=S.adopt_orphans(log))


def session_end(repo: Path, session: str, *, summary: str = "", agent: str = "") -> O.Outcome:
    from ..services import sessions as S

    log, _cfg, _st = _load(repo, agent)
    S.end(log, session, summary=summary)
    return O.ok("session.ended", session=session)


def history(
    repo: Path,
    *,
    item: str = "",
    kind: str = "",
    since: str = "",
    limit: int = 40,
    agent: str = "",
    by_agent: str = "",
    tail: int = 0,
) -> O.Outcome:
    """One reverse-chronological timeline of everything that happened.

    `by_agent` keeps one agent's shard only (`agent` is the CALLER's identity, not a
    filter). `tail=N` is the last N events oldest-first, like `tail`, and overrides `limit`.

    Ordered by `(lamport, agent, id)` like everything else — NOT by wall-clock timestamp.
    Two agents on two machines have two clocks, and sorting a merged history by `ts` would
    interleave them wrongly while looking perfectly plausible.
    """
    log, _cfg, _st = _load(repo, agent)
    events = log.read_all()
    if item:
        events = [e for e in events if e.subject == item]
    if kind:
        wanted = set(csv_list(kind))
        events = [e for e in events if e.kind in wanted or e.kind.split(".")[0] in wanted]
    if since:
        events = [e for e in events if e.ts >= since]
    if by_agent:
        events = [e for e in events if e.agent == by_agent]
    events = sorted(events, key=lambda e: (e.lamport, e.agent, e.id), reverse=True)
    shown = events[:tail][::-1] if tail and tail > 0 else events[:limit]
    data: dict[str, Any] = {
        "total": len(events),
        "shown": len(shown),
        "events": [
            {
                "id": e.id,
                "at": e.ts,
                "lamport": e.lamport,
                "agent": e.agent,
                "kind": e.kind,
                "subject": e.subject,
                "data": e.data,
            }
            for e in shown
        ],
        "_render": {"events": shown, "total": len(events)},
    }
    if not shown:
        return O.nothing("history", "Nothing in the history matches.", **data)
    return O.ok("history", **data)


# -- operational memory ------------------------------------------------------------------


def memory_add(
    repo: Path,
    text: str,
    *,
    tags: str = "",
    id: str = "",
    answer: DD.Answer | None = None,
    agent: str = "",
) -> O.Outcome:
    """Remember one operational fact about this machine, repository or working state.

    Refused over `[memory] max_chars`, not truncated: a memory cut mid-sentence says
    something its author did not, and the refusal tells them to write a lesson or a
    journal note instead -- which is what a paragraph is.
    """
    log, cfg, st = _load(repo, agent)
    text = " ".join((text or "").split())
    if not text:
        return O.failed("memory.recorded", "a memory needs text", id="")
    limit = cfg.memory.max_chars
    if len(text) > limit:
        return O.failed(
            "memory.recorded",
            f"{len(text)} characters is over [memory] max_chars ({limit}). A memory is ONE "
            f"operational fact; a rule belongs in `lesson add`, what happened in "
            f"`session note`.",
            id="",
        )
    mid = id or auto_id("M", text)
    chk = DD.check_add(
        repo,
        log,
        cfg,
        st,
        DD.Record(kind="memory", event_kind="memory.recorded", rid=mid, body=text),
        answer,
    )
    if chk.refusal is not None:
        return chk.refusal
    if chk.extension:
        return DD.extend(log, cfg, chk, "memory.recorded")
    data: dict[str, Any] = {"text": text, **chk.fields}
    if tags:
        # Only when given: the fold MERGES, keeping a field the event omits, and an
        # always-present `tags: []` made correcting a fact by `--id` wipe its tags
        # (cross-family critic).
        data["tags"] = csv_list(tags)
    with log.transaction():
        log.append("memory.recorded", mid, data)
        DD.after_add(log, cfg, mid, chk)
    replaced = bool(id) and id in st.memories
    return O.ok("memory.recorded", id=mid, replaced=replaced, **chk.data())


def _memory_row(m) -> dict[str, Any]:
    return {
        "id": m.id,
        "text": m.text,
        "tags": m.tags,
        "at": m.origin_at or m.at,
        "by": m.by,
        "source": m.source,
        "forgotten": m.forgotten,
    }


def memory_list(
    repo: Path,
    *,
    query: str = "",
    limit: int = 0,
    include_forgotten: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Live memories, newest first; `query` ranks them instead; `include_forgotten` adds
    the ones the project stopped believing.

    Newest first because memory is operational state, and the latest word on "which
    GPUs are free" is the one that matters.
    """
    log, cfg, st = _load(repo, agent)
    if query:
        store = _store(repo, log, cfg)
        # `limit` defaults to ALL here as on the other path; a silent cap of 20 returned
        # a truncated answer presented as complete (roborev 825).
        ids = [r["id"] for r in store.search("memories", query, limit or max(1, len(st.memories)))]
        rows = [st.memories[i] for i in ids if i in st.memories]
        if include_forgotten:
            # The index holds LIVE memories only, so a forgotten one must be matched
            # here -- or `--all` means nothing whenever `--query` is given (roborev 825).
            terms = [t.lower() for t in query.split() if t]
            rows += [
                m
                for m in st.memories.values()
                if not m.live and any(t in m.text.lower() for t in terms)
            ]
    else:
        rows = sorted(
            (m for m in st.memories.values() if include_forgotten or m.live),
            key=lambda m: (m.origin_at or m.at, m.at),
            reverse=True,
        )
        if limit:
            rows = rows[:limit]
    data = {
        "memories": [_memory_row(m) for m in rows],
        "total_live": sum(1 for m in st.memories.values() if m.live),
    }
    if not rows:
        return O.nothing(
            "memory.list", "no memories" + (f" match {query!r}" if query else ""), **data
        )
    return O.ok("memory.list", **data)


def memory_forget(repo: Path, id: str, *, reason: str = "", agent: str = "") -> O.Outcome:
    """Stop believing a memory. It is kept, with the reason -- "we thought X until Y" is
    what stops the next agent re-learning X."""
    log, _cfg, st = _load(repo, agent)
    if not reason.strip():
        return O.failed(
            "memory.forgotten", "--reason is required: why is it no longer true?", id=id
        )
    m = st.memories.get(id)
    if m is None:
        return O.failed("memory.forgotten", f"no such memory {id!r}", id=id)
    if not m.live:
        return O.nothing("memory.forgotten", f"{id} is already forgotten: {m.forgotten}", id=id)
    log.append("memory.forgotten", id, {"reason": reason.strip()})
    return O.ok("memory.forgotten", id=id)
