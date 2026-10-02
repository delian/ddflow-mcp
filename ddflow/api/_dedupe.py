"""The add-time duplicate check, shared by every add (decision D-no-duplicates).

Each add path -- task, phase, bug, lesson, decision, research, memory; never a session
prompt or note -- calls ``check_add`` BEFORE it writes, and applies what comes back:

* a **refusal** (exit 3, "possible duplicate") carrying the candidates, when the text
  looks like an existing record and nobody has said what it is;
* an **extension** -- the text goes onto the existing record as a ``record.extended``
  and no new id is made -- when the answer is ``extends X`` / ``duplicate_of X`` and X is
  still open and unclaimed (or the text is an exact copy of such a record);
* otherwise **fields** for the add event: a link to X when X is claimed, in progress or
  closed (a NEW record, so nothing is lost), and the ``dedupe`` answer, which is recorded
  for every answer including ``new``.

``check_add`` is a function, not only a step inside the add paths: the importer calls it
(or passes ``on_match = "off"`` through the config it hands over) to decide how a batch
of records meets what is already filed.

The answer is ONE small record (``Answer``) rather than another loose keyword on every
add function: ``task_add`` already carries twelve (BACKLOG B179).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import outcome as O
from ..core import textsim

#: What an add can be answered with. ``new``: not a duplicate, record it. The others point
#: at an existing record (``Answer.target``).
RELATIONS = ("new", "extends", "duplicate_of", "related")
#: Words listed to explain one match: enough to see why, few enough to read at a glance.
SHARED_WORDS = 5


@dataclass(frozen=True)
class Answer:
    """What the adder says about a possible duplicate: ``new``, or ``extends`` /
    ``duplicate_of`` / ``related`` plus the record it points at."""

    relation: str = ""
    target: str = ""
    #: A dry run: ``check_add`` writes nothing and returns the candidates (``--check``,
    #: ``check_only``). The add function returns it as it returns a refusal.
    check_only: bool = False

    @classmethod
    def parse(cls, spec: str) -> Answer:
        """``new``, ``extends X``, ``duplicate_of X`` (also ``duplicate X``), ``related X``;
        a colon or equals sign may stand for the space. Empty is no answer. Anything else
        keeps its relation so ``problem`` can say what is wrong with it."""
        words = (spec or "").replace(":", " ").replace("=", " ").split()
        if not words:
            return cls()
        rel = {"duplicate": "duplicate_of", "dup": "duplicate_of"}.get(words[0], words[0])
        return cls(rel, " ".join(words[1:]))

    def __bool__(self) -> bool:
        return bool(self.relation)

    @property
    def problem(self) -> str:
        if not self.relation:
            return ""
        if self.relation not in RELATIONS:
            return f"unknown answer {self.relation!r}: one of {', '.join(RELATIONS)}"
        if self.relation == "new" and self.target:
            return "'new' names no record"
        if self.relation != "new" and not self.target:
            return f"{self.relation!r} needs the id of the record it points at"
        return ""


@dataclass
class Checked:
    """The verdict on one add. At most one of ``refusal`` / ``extension`` is set."""

    refusal: O.Outcome | None = None
    #: The existing record the text was added to instead of making a new one:
    #: {target, kind, relation, text, score, auto}. Applied by ``extend``.
    extension: dict[str, Any] | None = None
    #: Merged into the add event's data: ``extends`` / ``duplicate_of`` / ``related`` and
    #: the ``dedupe`` answer.
    fields: dict[str, Any] = field(default_factory=dict)
    #: Candidates (id, kind, title, state, score, shared) the adder was shown.
    shown: list[dict[str, Any]] = field(default_factory=list)
    #: Set when the check could not run (an index that would not open); the add goes ahead.
    unavailable: str = ""
    #: Who is told, for a link onto a record somebody is working: {id, state, holder} --
    #: ``holder`` is the agent holding its lease, '' when nobody does (it is closed).
    notify: dict[str, str] = field(default_factory=dict)
    #: ``related`` also links the other way, after the add: [(subject, target)].
    back_links: list[tuple[str, str]] = field(default_factory=list)

    def data(self) -> dict[str, Any]:
        """What an add's result carries about the check, whatever the outcome."""
        out: dict[str, Any] = {}
        if self.shown:
            out["candidates"] = self.shown
        answer = self.fields.get("dedupe")
        if answer:
            out["dedupe"] = answer
        for relation in ("extends", "duplicate_of", "related"):
            if relation in self.fields:
                out[relation] = self.fields[relation]
        if self.notify:
            out["holder"] = self.notify
        if self.unavailable:
            out["dedupe_unavailable"] = self.unavailable
        return out


def kind_of(st, rid: str) -> str:
    """The kind of the record ``rid``, or '' when the log holds no such record."""
    it = st.items.get(rid)
    if it is not None and not it.removed:
        return it.kind
    for kind, table in (
        ("bug", st.bugs),
        ("lesson", st.lessons),
        ("decision", st.decisions),
        ("research", st.research),
    ):
        if rid in table:
            return kind
    mm = st.memories.get(rid)
    return "memory" if mm is not None and mm.live else ""


def record_state(state, rid: str, kind: str) -> tuple[str, str]:
    """(headline, state) of one indexed record, as a person reads them: an item's title
    and where it is in the queue, a bug's summary and whether it is fixed."""
    if kind in ("task", "phase"):
        it = state.items.get(rid)
        if it is None:
            return "", "unknown"
        if it.lease and not it.lease.expired_at and it.state not in ("done", "abandoned"):
            return it.title, f"claimed by {it.lease.holder}"
        return it.title, it.state
    if kind == "bug":
        bg = state.bugs.get(rid)
        if bg is None:
            return "", "unknown"
        return bg.summary, bg.resolution or "open"
    if kind == "lesson":
        ls = state.lessons.get(rid)
        return (ls.title, "superseded" if ls.superseded_by else "active") if ls else ("", "unknown")
    if kind == "decision":
        dc = state.decisions.get(rid)
        return (dc.title, dc.status or "active") if dc else ("", "unknown")
    if kind == "research":
        rs = state.research.get(rid)
        return (rs.question, rs.verdict.lower() or "recorded") if rs else ("", "unknown")
    if kind == "memory":
        mm = state.memories.get(rid)
        return (mm.text, "live") if mm else ("", "unknown")
    return "", "unknown"


def extendable(st, rid: str, kind: str) -> bool:
    """May text be appended to ``rid``? Only while nobody is relying on it as written: an
    OPEN, UNCLAIMED task or phase, an unresolved bug, a lesson or decision not superseded,
    research, a live memory. Claimed, running, done, fixed or superseded: file a new
    record that links to it instead."""
    if kind in ("task", "phase"):
        it = st.items.get(rid)
        return bool(it and it.state == "open" and not (it.lease and not it.lease.expired_at))
    if kind == "bug":
        bg = st.bugs.get(rid)
        return bool(bg and not bg.resolution)
    if kind == "lesson":
        ls = st.lessons.get(rid)
        return bool(ls and not ls.superseded_by)
    if kind == "decision":
        dc = st.decisions.get(rid)
        return bool(dc and not dc.superseded_by and dc.status in ("", "accepted", "proposed"))
    return kind in ("research", "memory")


def rows(st, matcher, cands, text: str) -> list[dict[str, Any]]:
    """Candidates as a person reads them: what it is, where it stands, how close it
    scored and which words it shares. One implementation for ``similar`` and the add
    check, so the two show the same thing."""
    from ..infra.store import similar_records

    n, df = matcher.doc_freq(textsim.tokens(text))
    records = {r["id"]: r for r in similar_records(st)}
    mine = set(textsim.tokens(text))
    out = []
    for c in cands:
        r = records.get(c.id, {"title": "", "body": ""})
        shared = set(textsim.tokens(r["title"], r["body"])) & mine
        ranked = sorted(shared, key=lambda t: (-textsim.idf(df.get(t, 0), n), t))
        headline, where = record_state(st, c.id, c.kind)
        out.append(
            {
                "id": c.id,
                "kind": c.kind,
                "title": " ".join((headline or r["body"]).split())[:200],
                "state": where,
                "score": c.score,
                "shared": ranked[:SHARED_WORDS],
                "flags": [f for f in c.flags if f != "same_item"],
            }
        )
    return out


def _reason(rows_: list[dict[str, Any]]) -> str:
    lines = ["refused: possible duplicate. It reads like:"]
    for r in rows_:
        why = ", ".join(r["flags"]) or ", ".join(r["shared"])
        lines.append(
            f"  {r['id']} ({r['kind']}, {r['state']}, score {r['score']:.2f}): "
            f"{r['title'][:100]}" + (f"  [{why}]" if why else "")
        )
    top = rows_[0]["id"]
    lines.append(
        f"Answer it: new (it is a different record), extends {top} / duplicate_of {top} "
        f"(the same thing -- added to {top} while it is open and unclaimed, else filed as "
        f"a new record linked to it), or related {top} (linked both ways). A bug that a "
        f"task will fix is filed against that task (--item)."
    )
    return "\n".join(lines)


def options(rows_: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The answers offered with a refusal: ``new``, then each relation to each candidate."""
    out = [{"relation": "new", "target": ""}]
    for r in rows_:
        out += [{"relation": rel, "target": r["id"]} for rel in RELATIONS[1:]]
    return out


@dataclass(frozen=True)
class Record:
    """The record about to be added, as the check reads it. ``event_kind`` is the add's
    event, so a refusal renders like the add; ``rid`` the id it will be filed under;
    ``item`` what it is filed against (a bug's item, a task's parent)."""

    kind: str
    event_kind: str
    rid: str
    title: str = ""
    body: str = ""
    item: str = ""

    @property
    def text(self) -> str:
        return "\n".join(x for x in (self.title, self.body) if x)


def _assess(repo: Path, log, cfg: Config, st, rec: Record) -> tuple[Any, list[dict], str]:
    """(assessment, candidate rows, why the check could not run)."""
    from ..infra.store import Store
    from ..services import similar as sim

    dd = cfg.dedupe
    if dd.on_match == "off" or rec.kind not in dd.kinds:
        return sim.Assessment("off"), [], ""
    try:
        store = Store(repo, cfg)
        store.ensure(log)
        with sim.open_store(store) as matcher:
            record = {
                "id": rec.rid,
                "kind": rec.kind,
                "title": rec.title,
                "body": rec.body,
                "item": rec.item,
            }
            found = sim.assess(matcher, record, cfg)
            shown = rows(st, matcher, found.candidates, rec.text) if found.candidates else []
            return found, shown, ""
    except (LookupError, OSError) as exc:
        return sim.Assessment("off"), [], f"{type(exc).__name__}: {exc}"


def _bad_answer(st, rec: Record, answer: Answer) -> str:
    bad = answer.problem
    if bad or not answer.target:
        return bad
    if answer.target == rec.rid:
        return "a record cannot point at itself"
    if not kind_of(st, answer.target):
        return f"no such record {answer.target!r} to point at"
    return ""


def check_add(
    repo: Path, log, cfg: Config, st, rec: Record, answer: Answer | None = None
) -> Checked:
    """Decide what an add of ``rec`` does. See the module docstring.

    ``st`` is the state the add was decided against. A ``rec.rid`` that already exists is
    not checked here (adding it again keeps the refusal or merge every add already has).
    Never raises for an index that cannot be read: the add goes ahead, and ``unavailable``
    says the check did not run.
    """
    answer = Answer() if answer is None else answer
    bad = _bad_answer(st, rec, answer)
    if bad:
        return Checked(refusal=O.failed(rec.event_kind, bad, id=rec.rid))
    if answer.check_only:
        return _dry_run(repo, log, cfg, st, rec)
    if rec.kind and kind_of(st, rec.rid) == rec.kind:
        return Checked()
    found, shown, unavailable = _assess(repo, log, cfg, st, rec)
    out = Checked(shown=shown, unavailable=unavailable)
    cands = [{"id": r["id"], "score": r["score"]} for r in shown]
    score = shown[0]["score"] if shown else None
    chosen, auto = answer, False
    if not chosen and found.action == "ask":
        exact = found.identical
        if exact is None or exact.kind != rec.kind:
            out.refusal = O.refused(
                rec.event_kind,
                _reason(shown),
                id=rec.rid,
                candidates=shown,
                options=options(shown),
            )
            return out
        chosen, auto = Answer("duplicate_of", exact.id), True
    if not chosen:
        if found.action == "warn":
            out.fields["dedupe"] = {"answer": "new", "score": score, "candidates": cands}
            out.fields["dedupe"]["warned"] = True
        return out
    if chosen.relation == "new":
        out.fields["dedupe"] = {"answer": "new", "score": score, "candidates": cands}
        return out
    hit = next((r for r in shown if r["id"] == chosen.target), None)
    note: dict[str, Any] = {
        "answer": chosen.relation,
        "target": chosen.target,
        "score": hit["score"] if hit else None,
        "candidates": cands,
    }
    if auto:
        note["auto"] = True
    return _point(st, rec, chosen, note, out)


def _dry_run(repo: Path, log, cfg: Config, st, rec: Record) -> Checked:
    """What an add of ``rec`` would meet, with nothing written: exit 0 with the candidates
    (and whether the add would be refused), exit 2 when none reads like it. Returned in
    ``Checked.refusal``, which every add already hands back unchanged."""
    found, shown, unavailable = _assess(repo, log, cfg, st, rec)
    exact = getattr(found, "identical", None)
    same_kind_copy = exact is not None and exact.kind == rec.kind
    data: dict[str, Any] = {
        "id": rec.rid,
        "check_only": True,
        "on_match": cfg.dedupe.on_match,
        "would_ask": found.action == "ask" and not same_kind_copy,
        # An exact copy is merged without asking: the id the text would be added to.
        "would_extend": exact.id if found.action == "ask" and same_kind_copy else "",
        "candidates": shown,
        "options": options(shown) if shown else [],
    }
    if unavailable:
        data["dedupe_unavailable"] = unavailable
        # NOT "nothing reads like it": the check did not run, and a caller gating on the
        # exit code must not read an outage as a clean bill.
        return Checked(
            refusal=O.failed(
                rec.event_kind, f"the duplicate check could not run: {unavailable}", **data
            )
        )
    if not shown:
        if cfg.dedupe.on_match == "off":
            why = "the check is off ([dedupe].on_match = off)"
        elif rec.kind not in cfg.dedupe.kinds:
            why = f"{rec.kind} is not checked ([dedupe].kinds = {', '.join(cfg.dedupe.kinds)})"
        else:
            why = f"nothing scores {cfg.dedupe.show_floor:g} or more against it"
        return Checked(
            refusal=O.nothing(rec.event_kind, f"no existing record reads like this: {why}", **data)
        )
    return Checked(refusal=O.ok(rec.event_kind, **data), shown=shown)


def _point(st, rec: Record, chosen: Answer, note: dict[str, Any], out: Checked) -> Checked:
    """Apply an answer that points at an existing record."""
    tkind = kind_of(st, chosen.target)
    if chosen.relation == "related":
        out.fields |= {"related": chosen.target, "dedupe": note}
        out.back_links.append((chosen.target, rec.rid))
    elif extendable(st, chosen.target, tkind):
        out.extension = {
            "target": chosen.target,
            "kind": tkind,
            "relation": chosen.relation,
            "text": rec.text,
            "score": note["score"],
            "auto": bool(note.get("auto")),
            "dedupe": note,
        }
    else:
        out.fields |= {chosen.relation: chosen.target, "dedupe": note}
        it = st.items.get(chosen.target)
        live = bool(it and it.lease and not it.lease.expired_at)
        out.notify = {
            "id": chosen.target,
            "state": record_state(st, chosen.target, tkind)[1],
            "holder": it.lease.holder if live and it and it.lease else "",
        }
    return out


def extend(log, cfg: Config, chk: Checked, event_kind: str) -> O.Outcome:
    """Append the text to the record it was answered onto. No new id is made; the
    outcome names the record that received it. The original text is never touched."""
    ex = chk.extension
    assert ex is not None
    log.append(
        "record.extended",
        ex["target"],
        {
            "text": ex["text"],
            "who": cfg.agent.id,
            "score": ex["score"],
            "relation": ex["relation"],
            "auto": ex["auto"],
            "dedupe": ex["dedupe"],
        },
    )
    return O.ok(
        event_kind,
        id=ex["target"],
        extended=ex["target"],
        extended_kind=ex["kind"],
        relation=ex["relation"],
        score=ex["score"],
        auto=ex["auto"],
        candidates=chk.shown,
    )


def after_add(log, cfg: Config, rid: str, chk: Checked) -> None:
    """The follow-up events of an add: ``related`` links the other way too."""
    for subject, target in chk.back_links:
        log.append(
            "link.recorded",
            subject,
            {"relation": "related", "target": target, "by": cfg.agent.id},
        )


def with_check(cfg: Config, **changes: Any) -> Config:
    """``cfg`` with some ``[dedupe]`` knobs replaced, for a caller that wants a different
    policy for a batch (the importer) without touching the project's config."""
    return dataclasses.replace(cfg, dedupe=dataclasses.replace(cfg.dedupe, **changes))
