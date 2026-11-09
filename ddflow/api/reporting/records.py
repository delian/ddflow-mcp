"""One record in full: `ddflow show`, and the bug-report addenda it and `brief` print."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...core import clock
from ...core import outcome as O
from ...core.plain import plain
from ...core.records import contested_summary, record_pools
from ...core.schedule import inherited_deps
from ...infra.worktree import absolutise
from ...services import gates as G
from ...services import ledger as LG
from ...services import reprobundle as RB
from ...services.completion import changed_paths, fix_task_for
from .._base import _load

#: How much of one record's text a rendering carries. The record is whole in the log and
#: in `show --json`; a brief is budgeted.
_TEXT_CAP = 600


def _epoch(ts: str) -> float:
    """An event's ISO timestamp as epoch seconds; 0.0 for one that does not parse."""
    return clock.epoch(ts, naive="local", default=0.0)


def _inherited_needs(st, it) -> list[str]:
    """The dependencies that bind `it`: its own `needs` plus every phase ancestor's,
    deduped in declaration order (`inherited_deps`). Printed so the graph shown is the
    one an operator actually has to satisfy, not only what `it` declares (B8981fa81bd)."""
    out: list[str] = []
    seen: set[str] = set()
    for _owner, dep in inherited_deps(st, it):
        if dep not in seen:
            seen.add(dep)
            out.append(dep)
    return out


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
    """One item, with its gate status -- or one bug or research note, by its id. The wire body is the item
    (or the bug's record) itself.

    Worktree paths are absolutised on the way out: the log stores them RELATIVE to the
    repo root, which is what makes a committed log true on every checkout, but a caller
    handed ".ddflow-worktrees/T1" has to know what it is relative to and will resolve it
    against its own cwd.
    """

    _log, cfg, st = _load(repo, agent)
    defs = G.load_gates(repo, cfg)
    it = st.items.get(item)
    if it is None and item in st.bugs:
        return _show_bug(st, st.bugs[item], repo, cfg)
    if it is None and item in st.research:
        return _show_research(st, st.research[item])
    if it is None or it.removed:
        if it is not None:
            return O.failed(
                "show", f"no such item {item!r} (it was removed from the queue)", id=item, item=None
            )
        return O.failed("show", f"no such item or bug {item!r}", id=item, item=None)

    def diff():
        return changed_paths(repo, it)

    led = LG.build(_log.read_all(), item) if it.state == "done" else None
    shown = absolutise(repo, plain(it))
    # A task's effective dependencies are its own `needs` plus every phase ancestor's;
    # print that, not only what the task declares itself (B8981fa81bd).
    shown["needs"] = _inherited_needs(st, it)
    return O.ok(
        "show",
        id=item,
        item={
            **shown,
            **addenda(st, item),
            **({"ledger": LG.summary(led)} if led else {}),
        },
        gates=plain(G.status(st, cfg, item, defs, diff=diff)),
        _render={
            "item": it,
            "gate_status": G.status(st, cfg, item, defs, diff=diff),
            "addenda": addenda(st, item),
        },
    )


def _show_research(st, note) -> O.Outcome:
    """A research note id handed to `show`: the note, its verdict, and what was filed to
    extend it (the `addenda`). The wire body (`item`) is the record itself."""
    record = {**plain(note), "kind": "research", "state": note.verdict, **addenda(st, note.id)}
    return O.ok("show", id=note.id, item=record, _render={"research": record})


def _show_bug(st, bug, repo=None, cfg=None) -> O.Outcome:
    """A bug id handed to `show` (B-show-bug-id): its record, its state, and the item that
    REALLY fixes it -- the one `complete <task>` checks (`fix_task_for`: the bug's `fix_task`
    field, else a live task whose `fixes` lists it or whose id follows the `fix-<bug>`
    template). An item whose title or body only SAYS "fixes bug X" is a claim, not the link,
    and is a mention, not a fix task (B0b1f135e37). The wire body (`item`, as for an item) is
    the record itself."""
    named = re.compile(rf"(?<![\w-]){re.escape(bug.id)}(?![\w-])")
    real = fix_task_for(st, bug.id, cfg) if cfg is not None else None
    fixing: list[str] = [real.id] if real is not None and not real.removed else []
    mentions: list[str] = []
    for i in sorted(st.items.values(), key=lambda x: x.id):
        text = f"{i.title or ''}\n{i.body or ''}"
        if i.removed or i.id in fixing or not named.search(text):
            continue
        mentions.append(i.id)
    record = {
        **plain(bug),
        "kind": "bug",
        "state": bug.resolution or "open",
        "fixing": fixing,
        "mentioned_by": mentions,
        **addenda(st, bug.id),
        **RB.shown(repo, bug.repro),
    }
    return O.ok("show", id=bug.id, item=record, _render={"bug": record})


def _contest_problems(st) -> list[str]:
    """B191: a merge brought in a rival definition or a rival claim. Every state, not only
    open ones -- a finished item whose other definition was dropped is still lost work.
    B230e852e10: the same for a decision, lesson or research id two agents defined."""
    out = [
        f"{it.id} is CONTESTED: {it.contest_summary()} — "
        f"`ddflow show {it.id}`, then `ddflow resolve {it.id} --keep <event-id|agent>`"
        for it in sorted(st.items.values(), key=lambda i: i.id)
        if not it.removed and it.contest_summary()
    ]
    for kind, pool in record_pools(st):
        out += [
            f"{kind} {r.id} is CONTESTED: {contested_summary(r.contested)} — "
            f"`ddflow resolve {r.id} --keep <event-id|agent>` settles it"
            for r in sorted(pool.values(), key=lambda x: x.id)
            if r.contested
        ]
    return out
