"""Queue status, progress and the loop findings: `ddflow status`, `progress`, `loops`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...config import Config
from ...core import outcome as O
from ...core.events import version_key
from ...core.model import fold
from ...core.plain import plain
from ...infra.log import EventLog
from ...services import gates as G
from ...views.markdown import may_hold_work
from .._base import _load


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
    from ...core import progress as PR

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
    from ...core import progress as PR

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


def status(repo: Path, *, agent: str = "", full: bool = False) -> O.Outcome:
    """One answer to "what is the state of this project?".

    ``full`` lists everything; otherwise each list is cut to ``STATUS_LIST_LIMIT`` (the
    most recently completed tasks, newest last; the first of the others, in the
    scheduler's order) and
    ``truncated`` names each cut list with its real length. The CLI asks for ``full``; the
    MCP tool takes the bounded answer.

    The textbook B37 case: `cmd_status` folded the log, aggregated the work, detected the
    loops, planned and scanned for recoverables — and then built a JSON object and a
    prose summary from that ONE computation, separately, by hand. Two of the numbers
    appeared in only one of them.
    """
    from ...core import progress as PR
    from ...core.schedule import plan
    from ...services import leases as L

    log, cfg, _ = _load(repo, agent)
    events = log.read_all()
    st = fold(events, strict=False)
    tracked = PR.work(events, st)
    findings = PR.detect(events, st, cfg)
    from ...services.flowstate import limit_for

    p = plan(st, cfg, agent=log.agent_id, parallel=limit_for(repo, cfg, st, events))
    rec = L.scan(log, cfg, repo)

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
    if taken:
        data["taken_over"] = [
            {"id": i, "holder": holder, "taken_over_by": by}
            for i, (holder, by) in sorted(taken.items())
        ]
    if st.skipped_kinds:
        data["skipped_kinds"] = dict(st.skipped_kinds)
    flagged = G.refuted_passes(st)
    if flagged:  # D-unify 5: a pass on refutation is always visible; absent when there is none
        data["passed_on_refutation"] = {
            "gates": len(flagged),
            "items": len({r["item"] for r in flagged}),
            "list": "ddflow gate list --refuted",
        }
    if st.highest_version:
        # Which ddflow versions have worked on this log, and the highest (the version stamp).
        data["ddflow_version"] = {
            "highest": st.highest_version,
            "seen": sorted(st.ddflow_versions, key=lambda v: (version_key(v), v)),
        }
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
        "recoverable": rec,
        "findings": findings,
        "hours": hours,
        "tasks": len(tasks),
        "phases": len(phases),
        "decisions": len(live_decisions),
        "lessons": len(st.lessons),
        "open_bugs": len(open_bugs),
        "refuted": len(flagged),
    }
    return O.ok("status", **data)


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
    if cut:
        data["truncated"] = {
            "lists": cut,
            "shown": STATUS_LIST_LIMIT,
            "all": "`ddflow --json status` lists every entry; `tasks` counts are exact",
        }
