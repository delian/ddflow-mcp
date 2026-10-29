"""Queue status, progress and the loop findings: `ddflow status`, `progress`, `loops`."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...config import Config
from ...core import outcome as O
from ...core import progress as PR
from ...core.events import version_key
from ...core.model import fold
from ...core.plain import plain
from ...infra.log import EventLog
from ...services import gates as G
from ...services import leasebreak as LBR
from ...services import leases as L
from ...services import upgrade_check as UC
from ...services import waiting as WG
from ...services.approvals import view as V
from ...services.cleanup import removed_with_live_lease
from ...services.leaseclock import localize
from ...views.markdown import may_hold_work
from .._base import _load
from ..lifecycle.planning import plan_for
from .waitcycles import health_lines, wait_cycles


def loops(repo: Path) -> O.Outcome:
    """Circular references and runtime loops. Reads only.

    The shape B37 is about: ONE description of the result, from which both surfaces
    derive their view. `cmd_loops` used to build the JSON body and the human paragraph
    independently — two renderings of one answer, kept in step by hand, which is how
    `cmd_complete` came to print a coverage gap to humans only.

    Exit stays as it was: 1 when there are findings, 2 when there are none. "Loops
    found" is a finding rather than a tool failure, but that contract is what callers
    already branch on and changing it silently would be worse than its imperfection.
    """

    # cfg BEFORE the log, not after: the log needs `[log]` to honour `reuse_parsed`.
    cfg = Config.load(repo)
    log = EventLog(repo, log_cfg=cfg.log)
    events = log.read_all()
    st = fold(events, strict=False)
    findings = [f.__dict__ for f in PR.detect(events, st, cfg)]
    data: dict[str, Any] = {
        "findings": findings,
        "events": len(events),
        "items": len(st.items),
        "blocking": [f for f in findings if f.get("severity") == "block"],
        "checked": [
            "dependency cycles",
            "repeat claims",
            "gate flapping",
            "repeated failures",
            "reopened items",
            "duplicate work",
            "stalled queue",
        ],
    }
    if not findings:
        return O.nothing("loops", "no loops detected", **data)
    n, b = len(findings), len(data["blocking"])
    return O.failed("loops", f"{n} finding(s)" + (f", {b} blocking" if b else ""), **data)


def progress(repo: Path, item: str = "") -> O.Outcome:
    """What work has actually been done, aggregated from the log. Reads only.

    The wire body is the ROW ARRAY, exactly as `ddflow progress --json` emits it — see
    `MIGRATED_WIRE_SHAPES`. The extra keys here are for the human renderer and for
    callers that want the count without walking the list; the `payload` entry on the
    tool keeps the MCP body unchanged.
    """

    log = EventLog(repo, log_cfg=Config.load(repo).log)
    events = log.read_all()
    st = fold(events, strict=False)
    rows = [r for r in PR.work(events, st).values() if not item or r.item == item]
    rows.sort(key=lambda r: (-r.total_seconds, r.item))
    data: dict[str, Any] = {
        "rows": [r.summary() for r in rows],
        "count": len(rows),
        "item": item,
    }
    if item and not rows:
        return O.failed("progress", f"no such item {item!r}", **data)
    if not rows:
        return O.nothing("progress", "no work recorded yet", **data)
    return O.ok("progress", **data)


#: How many entries each of `status`'s lists carries unless the caller asks for them all.
#: The counts are always exact; a 4831-task queue listed every finished task, 721k chars,
#: past what an MCP client accepts as one tool result (Bd6aa9ffde9).
STATUS_LIST_LIMIT = 25

#: How many of the most-blocking open dependencies each `--by-tag` bucket names. The
#: counts below it stay exact; a queue like this one can have thousands of pairs.
BY_TAG_TOP_BLOCKERS = 5


def status(repo: Path, *, agent: str = "", full: bool = False, by_tag: str = "") -> O.Outcome:
    """One answer to "what is the state of this project?".

    ``full`` lists everything; otherwise each list is cut to ``STATUS_LIST_LIMIT`` (the
    most recently completed tasks, newest last; the first of the others, in the
    scheduler's order) and
    ``truncated`` names each cut list with its real length. The CLI asks for ``full``; the
    MCP tool takes the bounded answer.

    ``by_tag`` (the ``--by-tag KEY`` flag) adds the ``by_tag`` object: per-value counts
    for every distinct tag in the KEY namespace, read off the same plan, so one command
    answers "how is wave 3 doing, and what blocks it?" (DX-waves-status).

    The textbook B37 case: `cmd_status` folded the log, aggregated the work, detected the
    loops, planned and scanned for recoverables — and then built a JSON object and a
    prose summary from that ONE computation, separately, by hand. Two of the numbers
    appeared in only one of them.
    """

    # The fold `_load` made (localized, overlaid) answers every part of this view: the log
    # is folded ONCE per command (B-gap-log-scale; tests/test_fold_once.py).
    log, cfg, st = _load(repo, agent)
    events = log.read_all()
    if st.event_count != len(events):  # an append landed between `_load`'s read and this one
        st = localize(fold(events, strict=False), log, time.time())
    tracked = PR.work(events, st)
    findings = PR.detect(events, st, cfg)
    # The offer `next` makes (reservation hold and parallelism limit alike), so "ready" here is
    # never an item `next` withholds for a waiter in line.
    p = plan_for(repo, log, cfg, st, purpose="view", agent=log.agent_id, events=events)
    L.surface_contests(p, st, cfg)  # B2809f4213b: contested is not 'in flight'
    rec = L.scan(log, cfg, repo, state=st)

    phases, tasks = st.phases(), st.tasks()
    # Every bucket is read off the ONE plan `next` and `brief` use, so each task is in
    # exactly one and `total` is their sum (Bdcce70d036: "blocked" counted reason
    # "deps" alone, and every item a parallelism cap held back was in no bucket at all).
    done = [t for t in tasks if t.state == "done"]
    # The settled states are counted by the one per-state count (core.progress) the
    # STATUS.md export uses; the in-flight buckets come from the plan.
    counts = PR.state_counts(tasks)
    running = p.running
    capped = [st.items[i] for i in p.capped]
    blocked = [b for b in p.blocked if b.item not in set(p.capped)]
    hours = sum(w.total_seconds for w in tracked.values()) / 3600
    commits = sum(len(w.commits) for w in tracked.values())
    live_decisions = [d for d in st.decisions.values() if d.live]
    open_bugs = [b for b in st.bugs.values() if b.open]

    data: dict[str, Any] = {
        "phases": {"total": len(phases), "done": sum(1 for x in phases if x.state == "done")},
        "tasks": {
            "total": len(tasks),
            "done": counts["done"],
            "running": len(running),
            "ready": len(p.ready),
            "held_by_cap": len(capped),
            "blocked": len(blocked),
            "review": len(p.review),
            "abandoned": counts["abandoned"],
        },
        "completed_tasks": [
            {"id": t.id, "title": t.title, "sha": t.merged_sha}
            for t in sorted(done, key=lambda t: t.completed_at)
        ],
        "in_flight": [
            {"id": t.id, "title": t.title, "holder": t.lease.holder if t.lease else ""}
            for t in running
        ],
        # A task RUNNING with no live lease is offered as ready -- someone must resume it
        # -- but never silently: its worktree may hold uncommitted work. `next` and
        # `brief` say so; so does this (roborev, job 897).
        "ready_now": [
            {
                "id": t.id,
                "title": t.title,
                **({"interrupted": True} if t.state == "running" else {}),
            }
            for t in p.ready
        ],
        "interrupted": p.interrupted,
        "held_by_cap": [{"id": t.id, "title": t.title} for t in capped],
        "cap": p.cap_note if capped else "",
        # "parallel: 6 (auto: ceiling 8; limited by ...)" or "parallel: 4 (fixed)"
        "parallel": p.parallel_line,
        "agent_hours": round(hours, 2),
        "commits": commits,
        "decisions": len(live_decisions),
        "lessons": len(st.lessons),
        "open_bugs": len(open_bugs),
        "loops": [f.__dict__ for f in findings],
        "recoverable": [plain(r) for r in rec if may_hold_work(r)],
    }
    # D-contest-redisplay: a contestant displayed again after the claim that took it over
    # was released. Listed here, and only when there is one, so the shape is otherwise
    # unchanged.
    # Every item still in the queue -- usually blocked (contested), but a late renewal or
    # a contest that dissolved can leave it in flight or ready -- and the prose marks it
    # in whichever list it lands, so the two always agree.
    taken = {
        t.id: (t.lease.holder, by)
        for t in tasks
        if t.lease is not None
        and not t.removed
        and t.state not in ("done", "abandoned", "review")
        and (by := t.lease_taken_over_by())
    }
    contested = [L.contest_row(st, cfg, i) for i in L.contested_items(st)]
    if contested:
        data["contested"] = contested
    waits = WG.rows(st, cfg, time.time())
    if waits:
        data["waiting"] = waits  # who is refused which files, and by whom (no polling)
    # DX-lease-yield-doctor-status: the operator must see a contended, idle lease too, not
    # only the holder's brief/heartbeat. Wire body carries the facts; the human view the text.
    yield_vs = LBR.verdicts(st, cfg, events, time.time(), LBR.tree_activity(log.root))
    if yield_vs:
        data["yield_requested"] = [
            {
                "item": v.item,
                "holder": v.holder,
                "idle_s": round(v.idle_s, 1),
                "waiters": list(v.waiters),
                "breakable": v.breakable,
            }
            for v in yield_vs
        ]
    health = health_lines(log, st, cfg, repo, wait_cycles(st, cfg))
    if health:
        data["health"] = health  # stalled / locked / runaway lanes (`ddflow health`)
    if taken:
        data["taken_over"] = [
            {"id": i, "holder": holder, "taken_over_by": by}
            for i, (holder, by) in sorted(taken.items())
        ]
    stranded = removed_with_live_lease(st, cfg, log.clone_id(create=False))
    if stranded:
        data["stranded"] = (
            stranded  # removed, lease still live: in every clone, not only the holder's
        )
    if st.skipped_kinds:
        data["skipped_kinds"] = dict(st.skipped_kinds)
    flagged = G.refuted_passes(st)
    if flagged:  # D-unify 5: a pass on refutation is always visible; absent when there is none
        data["refuted_passes"] = {
            "gates": len(flagged),
            "items": len({r["item"] for r in flagged}),
            "list": "ddflow gate list --refuted",
        }
    if approvals := V.waiting_for_a_person(st):  # RA-cli: only when a request waits
        data["approvals_pending"] = [
            {
                "id": r["id"],
                "verb": r["verb"],
                "agent": r["requested_by"],
                "digest": r["digest"],
                "answer": f"ddflow approvals approve {r['id']}",
            }
            for r in approvals
        ]
    if st.highest_version:
        # Which ddflow versions have worked on this log, and the highest (the version stamp).
        data["ddflow_version"] = {
            "highest": st.highest_version,
            "seen": sorted(st.ddflow_versions, key=lambda v: (version_key(v), v)),
        }
    if release := UC.known_line(repo, cfg):  # cached answer only: status never asks the network
        data["release"] = release
    if by_tag:
        data["by_tag"] = _by_tag_view(by_tag, [*tasks, *phases], p)
    if not full:
        _bound(data)
    # Carried for the prose view, which needs the OBJECTS (`completed_at` to sort by, the
    # blocked ids, how many recoverables are not salvageable) rather than a second fold.
    # Under `_render`, never on the wire.
    data["_render"] = {
        "repo": repo.name,
        "done": done,
        "running": running,
        "ready": p.ready,
        "interrupted": p.interrupted,
        "capped": capped,
        "cap": p.cap_note,
        "parallel": p.parallel_line,
        "blocked": blocked,
        "taken_over": {i: by for i, (_holder, by) in taken.items()},
        "contested": contested,
        "waiting": [WG.line(w) for w in waits],
        "yields": [LBR.yield_text(v) for v in yield_vs],
        "stranded": [x["text"] for x in stranded],
        "health": health,
        "recoverable": rec,
        "findings": findings,
        "hours": hours,
        "tasks": len(tasks),
        "phases": len(phases),
        "decisions": len(live_decisions),
        "lessons": len(st.lessons),
        "open_bugs": len(open_bugs),
        "refuted": len(flagged),
        "approvals": len(data.get("approvals_pending", ())),
        "approvals_pending": data.get("approvals_pending", []),
        "release": release,
        "by_tag_lines": _by_tag_lines(data["by_tag"]) if by_tag else [],
    }
    return O.ok("status", **data)


def _by_tag_bucket(
    members, tag, ready_ids, running_ids, review_ids, capped, blocked, done_ids
) -> dict[str, Any]:
    """One bucket: the counts for every member carrying ``tag`` and what blocks them.

    ``done``/``abandoned`` close the item's own story; every other member counts as
    ``open`` and into the one plan bucket that holds it (running, review, ready,
    held_by_cap or blocked -- the plan puts each task in exactly one, Bdcce70d036).
    """
    states = dict.fromkeys(
        (
            "total",
            "done",
            "running",
            "ready",
            "review",
            "held_by_cap",
            "blocked",
            "abandoned",
            "open",
        ),
        0,
    )
    reasons: dict[str, int] = {}
    blockers: dict[str, int] = {}
    for it in members:
        states["total"] += 1
        if it.state == "done":
            states["done"] += 1
        elif it.state == "abandoned":
            states["abandoned"] += 1
        else:
            states["open"] += 1
            iid = it.id
            if iid in running_ids:
                states["running"] += 1
            elif iid in review_ids:
                states["review"] += 1
            elif iid in ready_ids:
                states["ready"] += 1
            elif iid in capped:
                states["held_by_cap"] += 1
            if iid in blocked:
                states["blocked"] += 1
                for b in blocked[iid]:
                    reasons[b.reason] = reasons.get(b.reason, 0) + 1
                    if b.reason == "deps":
                        for dep in b.waiting_on:
                            if dep not in done_ids:
                                blockers[dep] = blockers.get(dep, 0) + 1
    tops = sorted(blockers.items(), key=lambda kv: (-kv[1], kv[0]))[:BY_TAG_TOP_BLOCKERS]
    row = {
        "tag": tag,
        "states": states,
        "blocked_by": dict(sorted(reasons.items())),
        "top_blockers": [{"id": dep, "blocks": n} for dep, n in tops],
    }
    if len(blockers) > BY_TAG_TOP_BLOCKERS:
        # Bounded like the other status lists (Bd6aa9ffde9), and never silently: the cut
        # is named here the way `truncated` names the top panel's cuts.
        row["blockers_cut"] = len(blockers) - BY_TAG_TOP_BLOCKERS
    return row


def _by_tag_view(key: str, items: list[Any], p) -> dict[str, Any]:
    """The ONE `--by-tag KEY` description the JSON body and the prose lines share.

    A tag is in KEY's namespace when it IS ``key`` or starts with ``key + ":"`` (D-labels:
    ``wave:N``, ``track:fix``). Each distinct such tag is a bucket with the counts
    done/running/ready/review/held_by_cap/blocked/abandoned/open, the blocked reasons and
    the open dependencies blocking the most of its items; items carrying none of the
    namespace land in ``untagged``. An item carrying several of the namespace counts in
    every bucket it carries (labels are set-valued; the derived ``wave:N`` is one per
    item). Every count is read off the ONE plan `status` folds — ``ready`` here is exactly
    what `next` offers, ``blocked`` the plan's deps/conflict reasons minus the cap-held —
    so the per-value view and the top panel agree (Bdcce70d036). ``open`` = neither done
    nor abandoned; a phase member counts only there (its tasks are the queue's items).
    """

    def in_ns(tags: list[str]) -> list[str]:
        return [t for t in (tags or []) if t == key or t.startswith(key + ":")]

    buckets: dict[str, list[Any]] = {}
    untagged: list[Any] = []
    for it in items:
        if it.removed:
            continue
        tags = in_ns(it.tags)
        for t in tags:
            buckets.setdefault(t, []).append(it)
        if not tags:
            untagged.append(it)
    ready_ids = {i.id for i in p.ready}
    running_ids = {i.id for i in p.running}
    review_ids = {i.id for i in p.review}
    capped = set(p.capped)
    blocked: dict[str, list[Any]] = {}
    for b in p.blocked:
        if b.item not in capped:
            blocked.setdefault(b.item, []).append(b)
    # A blocker is an unsatisfied prerequisite as `dep_status` (core.schedule) settles it:
    # DONE returns satisfied (so a `done` id in `waiting_on` is a buggy writer and stays
    # out of `top_blockers`), ABANDONED counts as unsatisfied BY DESIGN (the dependent
    # stays blocked and the abandoned prerequisite is exactly the blocker to show). An id
    # the view has never seen (external `repo:item`, a deleted item) still counts -- the
    # plan reported it unsatisfied, and it reads as a blocker.
    done_ids = {it.id for it in items if it.state == "done"}
    shared = (ready_ids, running_ids, review_ids, capped, blocked, done_ids)
    return {
        "key": key,
        "values": [_by_tag_bucket(buckets[t], t, *shared) for t in sorted(buckets)],
        "untagged": _by_tag_bucket(untagged, "untagged", *shared),
    }


def _by_tag_lines(view: dict[str, Any]) -> list[str]:
    """The prose half of one ``by_tag`` description: the counts per value and, where a
    value's items sit blocked, who holds most of them up."""
    lines = [
        "",
        f"per {view['key']} value "
        "(done/running/ready/review/held/blocked/abandoned of total, then open):",
    ]
    for v in [*view["values"], view["untagged"]]:
        s = v["states"]
        lines.append(
            f"  {v['tag']:<12} {s['done']}/{s['running']}/{s['ready']}/{s['review']}/"
            f"{s['held_by_cap']}/{s['blocked']}/{s['abandoned']} of {s['total']}"
            f" (open {s['open']})"
        )
        if v["blocked_by"]:
            reasons = ", ".join(f"{r}({n})" for r, n in sorted(v["blocked_by"].items()))
            lines.append(f"    blocked by: {reasons}")
        if v["top_blockers"]:
            tops = ", ".join(f"{t['id']} ({t['blocks']})" for t in v["top_blockers"])
            if v.get("blockers_cut"):
                tops += f", {v['blockers_cut']} more"
            lines.append(f"    top blockers: {tops}")
    return lines


def _bound(data: dict[str, Any]) -> None:
    """Cut `status`'s lists to `STATUS_LIST_LIMIT`, saying which were cut and from what."""
    cut: dict[str, int] = {}
    lists = ("completed_tasks", "in_flight", "ready_now", "held_by_cap", "interrupted")
    for key in (*lists, "loops", "recoverable"):
        rows = data[key]
        if len(rows) > STATUS_LIST_LIMIT:
            cut[key] = len(rows)
            # The most recent completions are the ones a reader asks about -- kept in
            # completion order, newest last, as the full list has them; for the others
            # the scheduler's order puts what to do first at the top.
            keep = (
                slice(-STATUS_LIST_LIMIT, None)
                if key == "completed_tasks"
                else slice(STATUS_LIST_LIMIT)
            )
            data[key] = rows[keep]
    # The by-tag view is a list too: a namespace with thousands of distinct values (an
    # `assignee:` label space) is exactly the unbounded answer Bd6aa9ffde9 is about, so
    # the bounded MCP path cuts it the same way (the first values, lowest tag first).
    if "by_tag" in data:
        values = data["by_tag"]["values"]
        if len(values) > STATUS_LIST_LIMIT:
            cut["by_tag"] = len(values)
            data["by_tag"]["values"] = values[:STATUS_LIST_LIMIT]
    if cut:
        data["truncated"] = {
            "lists": cut,
            "shown": STATUS_LIST_LIMIT,
            "all": "`ddflow --json status` lists every entry; `tasks` counts are exact",
        }
