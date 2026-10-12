"""A recorded multi-lane log, built event by event with fixed times (no clock, no git).

Used by the lane ledger tests: the same events always fold to the same state, so a table of
lane states can be pinned.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ddflow.config import Config
from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.core.schedule import plan

T0 = 1_800_000_000.0
NOBODY = "\0lanes"


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z")


class Log:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def add(self, ekind: str, subject: str, agent: str, t: float, **data) -> None:
        e = Event(
            kind=ekind,
            subject=subject,
            data=data,
            agent=agent,
            lamport=len(self.events) + 1,
            ts=iso(t),
        )
        self.events.append(Event(**{**e.__dict__, "id": e.compute_id()}))

    def task(self, tid: str, globs: str = "", needs: str = "") -> None:
        self.add(
            "task.added",
            tid,
            "lead",
            T0 - 1000,
            title=f"task {tid}",
            body=f"do {tid}",
            globs=[g for g in globs.split(",") if g],
            needs=[n for n in needs.split(",") if n],
        )

    def claim(self, agent: str, tid: str, t: float, globs: str = "") -> None:
        self.add(
            "lease.acquired",
            tid,
            agent,
            t,
            holder=agent,
            at=t,
            ttl_s=1800,
            kind="task",
            globs=[g for g in globs.split(",") if g],
            worktree=f"/wt/{agent}",
        )
        self.add("item.started", tid, agent, t)

    def beat(self, agent: str, tid: str, t: float) -> None:
        self.add("lease.renewed", tid, agent, t, holder=agent, at=t)

    def release(self, agent: str, tid: str, t: float) -> None:
        self.add("lease.released", tid, agent, t, holder=agent, by=agent)

    def complete(self, agent: str, tid: str, t: float) -> None:
        self.add("item.completed", tid, agent, t)

    def session(self, agent: str, sid: str, t: float, *, ended: float = 0.0, **data) -> None:
        self.add("session.started", sid, agent, t, **data)
        if ended:
            self.add("session.ended", sid, agent, ended, summary="stopped early")


def multi_lane() -> Log:
    """Six lanes: working, stale, lapsed with no session end, lapsed after a session end,
    idle beside ready work, and a task chain nobody can start."""
    g = Log()
    for tid, globs in (("X", "f.py"), ("Y", "f.py"), ("Z", "z.py"), ("V", "v.py"), ("Q", "q.py")):
        g.task(tid, globs)
    g.task("U", "u.py")
    g.task("W", "w.py", needs="Z")
    g.session("lane-work", "s1", T0, tool="claude-code", model="opus")
    g.claim("lane-work", "X", T0, "f.py")
    g.beat("lane-work", "X", T0 + 600)
    g.claim("lane-stale", "Z", T0, "z.py")
    g.beat("lane-stale", "Z", T0 + 100)
    g.session("lane-gone", "s2", T0 - 400, tool="codex", model="gpt")
    g.claim("lane-gone", "U", T0 - 300, "u.py")
    g.beat("lane-gone", "U", T0 - 200)
    g.session("lane-ended", "s3", T0 - 400, ended=T0 - 100, tool="claude-code", model="opus")
    g.claim("lane-ended", "V", T0 - 300, "v.py")
    g.claim("lane-idle", "Q", T0 - 50, "q.py")
    g.release("lane-idle", "Q", T0 + 30)
    return g


def build_inputs(events: list[Event], now: float):
    cfg = Config()
    st = fold(list(events), strict=False)
    return st, cfg, plan(st, cfg, now=now, agent=NOBODY)
