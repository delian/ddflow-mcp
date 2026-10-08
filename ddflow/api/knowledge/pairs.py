"""Records that say the same thing: the duplicate sweep (`dupes`) and linking two records."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ...config import Config, csv_list
from ...core import outcome as O
from ...core.model import LINK_RELATIONS
from .._base import _load


def _sweep_records(st) -> list[dict[str, str]]:
    """Every record the pair sweep weighs, including the ones the ordinary index drops.

    A sweep is over the history that EXISTS, not only what is live: a removed item (filed
    and then taken back, frequently AS a duplicate -- B203 is one) and a superseded or
    forgotten record are exactly the pairs worth settling. `infra.store.similar_records`
    is the one definition of what a record's TEXT is, so this adds the dropped records to
    it rather than re-deriving titles and bodies.
    """
    from ...infra.store import similar_records

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

    kind, rid = rec["kind"], rec["id"]
    if kind in ("task", "phase"):
        it = st.items.get(rid)
        return bool(it and not it.removed and not it.terminal)
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
    stop_after: int = 0,
) -> list[dict[str, Any]]:
    """The pairs among ``records`` (id, kind, title, body) scoring at least ``floor``.

    The engine is `services.similar`'s exact TF-IDF cosine, asked of every record in
    turn; a canonical (a, b) key keeps each pair once, at its best score. Pure function
    of the records, so the sweep over a folded State and a sweep over a fixture are the
    SAME code -- which is how the acceptance set is checked without rebuilding a log.
    ``settled(a, b)`` asks whether the pair has already been answered. ``stop_after``
    (> 0) stops as soon as that many pairs are found, for a caller that only needs a
    bounded count (the doctor note) and must not pay for a dense log's full sweep.
    """
    from ...services import similar as sim

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
            if stop_after and len(best) >= stop_after:
                rows = sorted(best.values(), key=lambda x: (-x["score"], x["a"], x["b"]))
                return _titled(rows, by_id)
    rows = sorted(best.values(), key=lambda x: (-x["score"], x["a"], x["b"]))
    return _titled(rows, by_id)


def _titled(rows: list[dict[str, Any]], by_id: dict[str, dict]) -> list[dict[str, Any]]:
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
    stop_after: int = 0,
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
    rows = pair_records(
        records, floor=fl, settled=lambda a, b: _settled(st, a, b), stop_after=stop_after
    )
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


def _record_kind(st, rid: str) -> str:
    """The kind of a record the SWEEP can see, live or not.

    `_dedupe.kind_of` answers for LIVE records and returns "" for a removed item or a
    forgotten memory. The sweep deliberately weighs those (B203 was removed as a
    duplicate), so `ddflow link` must be able to settle the pairs `dupes` shows: this
    resolves a removed item and a forgotten memory too.
    """
    kind = DD.kind_of(st, rid)
    if kind:
        return kind
    it = st.items.get(rid)
    if it is not None and it.removed:
        return it.kind
    m = st.memories.get(rid)
    if m is not None:
        return "memory"
    return ""


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
    skind, tkind = _record_kind(st, subject), _record_kind(st, target)
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
