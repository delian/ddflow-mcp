"""One record in full: `ddflow show`, and the bug-report addenda it and `brief` print."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...core.plain import plain
from .._base import _load

#: How much of one record's text a rendering carries. The record is whole in the log and
#: in `show --json`; a brief is budgeted.
_TEXT_CAP = 600


def _epoch(ts: str) -> float:
    """An event's ISO timestamp as epoch seconds; 0.0 for one that does not parse."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def record_summary(st, rid: str) -> dict[str, Any]:
    """What a link points at, in one dict: its kind, title, state and own text."""
    if rid in st.items:
        i = st.items[rid]
        return {"kind": i.kind, "title": i.title or "", "state": i.state, "text": i.body or ""}
    if rid in st.bugs:
        b = st.bugs[rid]
        return {"kind": "bug", "title": "", "state": b.resolution or "open", "text": b.summary}
    if rid in st.lessons:
        ls = st.lessons[rid]
        return {"kind": "lesson", "title": ls.title, "state": "", "text": ls.text()}
    for kind, pool in (
        ("research", st.research),
        ("decision", st.decisions),
        ("memory", st.memories),
    ):
        if rid in pool:
            r = pool[rid]
            # `text` is a field on a memory and a METHOD on a decision: call what is callable.
            text = (
                getattr(r, "summary", "")
                or getattr(r, "text", "")
                or getattr(r, "claim", "")
                or getattr(r, "question", "")
                or ""
            )
            return {
                "kind": kind,
                "title": getattr(r, "title", "") or getattr(r, "question", "") or "",
                "state": "",
                "text": text() if callable(text) else text,
            }
    return {"kind": "", "title": "", "state": "", "text": ""}


def addenda(st, rid: str) -> dict[str, Any]:
    """Everything the log says was ADDED to, or LINKED to, one record (D-no-duplicates).

    `additions`: the verbatim `record.extended` texts, oldest first. `links`: what this
    record points at. `dismissals`: the `distinct` entries -- pairs somebody judged
    different, each with its `reason`. `linked_from`: the records that point at it --
    found by scanning every record's links for this target, because a new record filed
    `extends` / `duplicate_of` X is stored as a link ON THE NEW RECORD; State keeps no
    index under X and only `related` also writes a back-link.
    """
    mine = st.links.get(rid)
    links = []
    for x in mine.links if mine else []:
        links.append(
            {**x, **{k: v for k, v in record_summary(st, x["target"]).items() if k != "text"}}
        )
    # A `distinct` dismissal is an ANSWER about a pair, not a link, and `RecordLinks.links`
    # deliberately excludes it. It is listed here too, or the reason a pair was dismissed
    # (`ddflow link --distinct --reason`) would have no reader on any surface.
    dismissals = [
        {**x, **{k: v for k, v in record_summary(st, x["target"]).items() if k != "text"}}
        for x in (mine.dismissals if mine else [])
    ]
    out_related = {x["target"] for x in links if x["relation"] == "related"}
    inbound = []
    for src, rl in st.links.items():
        if src == rid:
            continue
        for x in rl.links:
            if x["target"] != rid:
                continue
            if x["relation"] == "related" and src in out_related:
                continue  # the back-link already listed it
            inbound.append({**x, "record": src, **record_summary(st, src)})
    inbound.sort(key=lambda x: (x["at"], x["event"], x["record"]))
    return {
        "additions": list(mine.extensions) if mine else [],
        "links": links,
        "dismissals": dismissals,
        "linked_from": inbound,
    }


def new_reports(st, rid: str, since: float) -> dict[str, Any]:
    """The additions and inbound links on `rid` made at or after `since` (epoch seconds):
    what was reported against an item after its holder claimed it."""
    a = addenda(st, rid)
    adds = [x for x in a["additions"] if _epoch(x["at"]) >= since]
    linked = [x for x in a["linked_from"] if _epoch(x["at"]) >= since]
    # A `related` record writes a back-link on X (a later `link.recorded`, never an add), which `addenda` lists once (as X's own
    # link) and not again as inbound -- but for a holder it IS a new report.
    for x in a["links"]:
        if x["relation"] == "related" and x["source"] == "later" and _epoch(x["at"]) >= since:
            linked.append(
                {**x, "record": x["target"], "text": record_summary(st, x["target"])["text"]}
            )
    return {"count": len(adds) + len(linked), "additions": adds, "linked_from": linked}


def show(repo: Path, item: str, *, agent: str = "") -> O.Outcome:
    """One item, with its gate status -- or one bug, by its id. The wire body is the item
    (or the bug's record) itself.

    Worktree paths are absolutised on the way out: the log stores them RELATIVE to the
    repo root, which is what makes a committed log true on every checkout, but a caller
    handed ".ddflow-worktrees/T1" has to know what it is relative to and will resolve it
    against its own cwd.
    """
    from ...infra.worktree import absolutise
    from ...services import gates as G

    _log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None and item in st.bugs:
        return _show_bug(st, st.bugs[item])
    if it is None or it.removed:
        if it is not None:
            return O.failed(
                "show", f"no such item {item!r} (it was removed from the queue)", id=item, item=None
            )
        return O.failed("show", f"no such item or bug {item!r}", id=item, item=None)
    from ...services import ledger as LG

    led = LG.build(_log.read_all(), item) if it.state == "done" else None
    return O.ok(
        "show",
        id=item,
        item={
            **absolutise(repo, plain(it)),
            **addenda(st, item),
            **({"ledger": LG.summary(led)} if led else {}),
        },
        gates=plain(G.status(st, cfg, item)),
        _render={
            "item": it,
            "gate_status": G.status(st, cfg, item),
            "addenda": addenda(st, item),
        },
    )


def _show_bug(st, bug) -> O.Outcome:
    """A bug id handed to `show` (B-show-bug-id): its record, its state, and the items
    that fix it -- those whose title or body say "fixes bug X" (or "fixes bugs A, X",
    "Fixing X"), the claim every fix task carries; any other mention is listed apart. The wire body (`item`, as for an item) is the record itself."""
    named = re.compile(rf"(?<![\w-]){re.escape(bug.id)}(?![\w-])")
    # The convention fix tasks follow: "fixes bug X", "fixes bugs A, B and X", "Fixing X"
    # -- the verb, then the id or a list holding it, with nothing else in between. Each
    # element before X ends in ",", ", and" or " and" (an element joined by a bare "and"
    # was never passed over, so "fixes bugs A and X" read as a mention: Bfc863d295f).
    claims_fix = re.compile(
        rf"\bfix(?:es|ed|ing)?\s+(?:bugs?\s+)?(?:[\w-]+(?:\s*,\s*(?:and\s+)?|\s+and\s+))*"
        rf"(?:and\s+)?{re.escape(bug.id)}(?![\w-])",
        re.I,
    )
    fixing: list[str] = []
    mentions: list[str] = []
    for i in sorted(st.items.values(), key=lambda x: x.id):
        text = f"{i.title or ''}\n{i.body or ''}"
        if i.removed or not named.search(text):
            continue
        # "fixes bug X" in the title or body is the fix's own claim; any other mention is
        # only that -- a task that discusses a bug is not its fix (roborev, job 954).
        (fixing if claims_fix.search(text) else mentions).append(i.id)
    record = {
        **plain(bug),
        "kind": "bug",
        "state": bug.resolution or "open",
        "fixing": fixing,
        "mentioned_by": mentions,
        **addenda(st, bug.id),
    }
    return O.ok("show", id=bug.id, item=record, _render={"bug": record})
