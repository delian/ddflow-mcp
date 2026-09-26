"""Read-only questions about the queue and the log. None of these write."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config
from ..core import outcome as O
from ..core.model import fold
from ..core.plain import plain
from ..infra.log import EventLog
from ._base import _load


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
    from ..core import progress as PR

    log = EventLog(repo)
    events = log.read_all()
    st = fold(events, strict=False)
    cfg = Config.load(repo)
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
    from ..core import progress as PR

    log = EventLog(repo)
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


def status(repo: Path, *, agent: str = "") -> O.Outcome:
    """One answer to "what is the state of this project?".

    The textbook B37 case: `cmd_status` folded the log, aggregated the work, detected the
    loops, planned and scanned for recoverables — and then built a JSON object and a
    prose summary from that ONE computation, separately, by hand. Two of the numbers
    appeared in only one of them.
    """
    from ..core import progress as PR
    from ..core.schedule import plan
    from ..services import leases as L

    log, cfg, _ = _load(repo, agent)
    events = log.read_all()
    st = fold(events, strict=False)
    tracked = PR.work(events, st)
    findings = PR.detect(events, st, cfg)
    p = plan(st, cfg, agent=log.agent_id)
    rec = L.scan(log, cfg, repo)

    phases, tasks = st.phases(), st.tasks()
    done = [t for t in tasks if t.state == "done"]
    running = [t for t in tasks if t.state == "running"]
    blocked = [b for b in p.blocked if b.reason == "deps"]
    hours = sum(w.total_seconds for w in tracked.values()) / 3600
    commits = sum(len(w.commits) for w in tracked.values())
    live_decisions = [d for d in st.decisions.values() if d.live]
    open_bugs = [b for b in st.bugs.values() if b.open]

    data: dict[str, Any] = {
        "phases": {"total": len(phases), "done": sum(1 for x in phases if x.state == "done")},
        "tasks": {
            "total": len(tasks),
            "done": len(done),
            "running": len(running),
            "ready": len(p.ready),
            "blocked": len(blocked),
        },
        "completed_tasks": [{"id": t.id, "title": t.title, "sha": t.merged_sha} for t in done],
        "in_flight": [
            {"id": t.id, "title": t.title, "holder": t.lease.holder if t.lease else ""}
            for t in running
        ],
        "ready_now": [{"id": t.id, "title": t.title} for t in p.ready],
        "agent_hours": round(hours, 2),
        "commits": commits,
        "decisions": len(live_decisions),
        "lessons": len(st.lessons),
        "open_bugs": len(open_bugs),
        "loops": [f.__dict__ for f in findings],
        "recoverable": [plain(r) for r in rec if r.salvageable],
    }
    # Carried for the prose view, which needs the OBJECTS (`completed_at` to sort by, the
    # blocked ids, how many recoverables are not salvageable) rather than a second fold.
    # Under `_render`, never on the wire.
    data["_render"] = {
        "repo": repo.name,
        "done": done,
        "running": running,
        "ready": p.ready,
        "blocked": blocked,
        "recoverable": rec,
        "findings": findings,
        "hours": hours,
        "tasks": len(tasks),
        "phases": len(phases),
        "decisions": len(live_decisions),
        "lessons": len(st.lessons),
        "open_bugs": len(open_bugs),
    }
    return O.ok("status", **data)


def rebuild(repo: Path, *, agent: str = "") -> O.Outcome:
    """Rebuild the sqlite projection from the log. The log is the source of truth; this
    is a cache, and saying how long it took is how you notice it has stopped being one."""
    import time

    from ..infra.store import Store

    log, cfg, _ = _load(repo, agent)
    t0 = time.time()
    st = Store(repo, cfg).rebuild(log)
    return O.ok(
        "rebuild",
        events=st.event_count,
        items=len(st.items),
        lessons=len(st.lessons),
        seconds=round(time.time() - t0, 2),
    )


def show(repo: Path, item: str, *, agent: str = "") -> O.Outcome:
    """One item, with its gate status. The wire body is the item itself.

    Worktree paths are absolutised on the way out: the log stores them RELATIVE to the
    repo root, which is what makes a committed log true on every checkout, but a caller
    handed ".ddflow-worktrees/T1" has to know what it is relative to and will resolve it
    against its own cwd.
    """
    from ..infra.worktree import absolutise
    from ..services import gates as G

    _log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed("show", f"no such item {item!r}{gone}", id=item, item=None)
    return O.ok(
        "show",
        id=item,
        item=absolutise(repo, plain(it)),
        gates=plain(G.status(st, cfg, item)),
        _render={"item": it, "gate_status": G.status(st, cfg, item)},
    )


def recover(repo: Path, *, item: str = "", apply: bool = False, agent: str = "") -> O.Outcome:
    """Crashed agents' leases and orphaned worktrees. Exit 2 when there is nothing.

    A worktree with uncommitted changes is reported and never touched, whatever `apply`
    says — the whole value of the sweep is that it does not destroy the work it found.
    """
    from ..infra.worktree import absolutise
    from ..services import leases as L

    log, cfg, _ = _load(repo, agent)
    found = [r for r in L.sweep(log, cfg, repo, apply=apply) if not item or r.item == item]
    salvageable = [r for r in found if r.salvageable]
    data: dict[str, Any] = {
        "found": [absolutise(repo, plain(r)) for r in found],
        "count": len(found),
        "salvageable": len(salvageable),
        "applied": apply,
        "_render": {"found": found, "salvageable": salvageable},
    }
    if not found:
        return O.nothing(
            "recover", "Nothing to recover — no expired leases, no orphan worktrees.", **data
        )
    return O.ok("recover", **data)
