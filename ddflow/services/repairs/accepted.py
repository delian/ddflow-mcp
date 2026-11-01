"""Accepted history: a permanent fact of an append-only log, acknowledged once by a person.

A secret scrub rewrites event lines (the id no longer matches the body) and two clones that
shared an agent id leave a Lamport decrease. Neither can be mended in a log nobody rewrites, so
`doctor` reported them as a PROBLEM forever and masked everything real (B-doctor-accepted-history).

`ddflow doctor --accept-history` records what is covered as a `repair.applied` event of the
repair id `accepted-history` (the record the quarantine repairs already use; no second store):
the keys of the findings, who, why, the check kind and the scrub's commit or note. A key binds
the acceptance to the finding as it is NOW -- a content mismatch to the event's id AND its current
content digest, a clock regression to the shard (by digest: an agent id holds a host name, which the log redacts), the event number and both clock values -- so
the same id with other content, or a new regression, is a PROBLEM again. Only a person may
accept (`refusal`), exactly as `--accept-lost-events` is the operator's alone.
"""

from __future__ import annotations

import getpass
from dataclasses import dataclass
from typing import Any

from ...core import digest as D
from ...core.events import REPAIR_APPLIED_KIND
from ...core.model import State
from ...infra.log import EventLog, running_version
from ..identity import agent_marker
from .base import Context, context

RID = "accepted-history"
CONTENT, CLOCK = "content_mismatch", "clock_regression"
#: Keys one `repair.applied` names, as `repairs.RECORD_FINDINGS` bounds every such line.
PER_EVENT = 100


@dataclass(frozen=True)
class Item:
    key: str
    check: str
    #: What `--history-only` names: an event id, or `<shard>:<event number>`.
    ref: str
    detail: str


def mismatches(events: list[Any]) -> list[Item]:
    return [
        Item(
            f"{CONTENT}:{e.id}:{e.compute_id()}",
            CONTENT,
            e.id,
            f"{e.id}: content does not match its address (edited after the fact?)",
        )
        for e in events
        if e.id and e.id != e.compute_id()
    ]


def regressions(log: EventLog) -> list[Item]:
    return [
        Item(
            f"{CLOCK}:{D.content_digest(shard, length=12)}:{n}:{before}:{after}",
            CLOCK,
            f"{shard}:{n}",
            f"{shard}: Lamport clock decreases at event {n} ({before} -> {after}) — two "
            "clones wrote as one agent id; give each its own (`DDFLOW_AGENT`, "
            "`[agent].id`, or drop both to derive a per-clone id)",
        )
        for shard, (n, before, after) in log.clock_regressions().items()
    ]


def records(st: State) -> dict[str, dict[str, Any]]:
    """Accepted finding key -> the record that accepted it (the latest wins)."""
    out: dict[str, dict[str, Any]] = {}
    for rec in st.repairs_applied.get(RID, ()):
        for key in rec["findings"]:
            out[key] = rec
    return out


def note(item: Item, rec: dict[str, Any]) -> str:
    scrub = f"; {rec['note']}" if rec.get("note") else ""
    return (
        f"accepted history ({item.check}) {item.detail.split(' — ')[0]} -- by "
        f"{rec.get('by') or rec['agent']} at {rec['at'][:16]}: {rec.get('reason', '')}{scrub}"
    )


def split(st: State, items: list[Item]) -> tuple[list[Item], list[str]]:
    """`(items still a PROBLEM, one note per accepted item)`."""
    done = records(st)
    open_ = [i for i in items if i.key not in done]
    return open_, [note(i, done[i.key]) for i in items if i.key in done]


def clock_findings(ctx: Context) -> tuple[list[str], list[str]]:
    """`(problems, notes)` for the Lamport regressions, less the accepted ones."""
    open_, notes = split(ctx.st, regressions(ctx.log))
    return [i.detail for i in open_], notes


def refusal(agent: str = "", reason: str = "") -> str:
    """Why this caller may not accept history ("" when it may): an agent is refused, and so
    is a call that gives no reason."""
    why = agent_marker(agent)
    if why:
        return (
            f"accepting history is a person's act ({why}): run `ddflow doctor --accept-history "
            "--history-reason WHY` yourself, in your own terminal; an agent reports the finding"
        )
    return "" if reason.strip() else "--accept-history needs --history-reason: say why it is known"


def accepting(ctx: Context, reason: str, note_: str = "", only: str = "") -> Context:
    """`accept` for the names in ``only`` (comma-separated), then a fresh read of the log."""
    names = tuple(x.strip() for x in only.split(",") if x.strip())
    accept(ctx, reason=reason, note_=note_, only=names)
    return context(ctx.repo, ctx.log, ctx.cfg)


def accept(ctx: Context, *, reason: str, note_: str = "", only: tuple[str, ...] = ()) -> list[Item]:
    """Record the findings now open as accepted (those named by ``only``, if given); returns
    them. Appends `repair.applied` events and nothing else; no line is edited."""
    log = ctx.log
    with log.transaction():
        fresh = [*mismatches(log.read_all()), *regressions(log)]
        done = records(ctx.st)
        todo = [i for i in fresh if i.key not in done and (not only or i.ref in only)]
        for at in range(0, len(todo), PER_EVENT):
            part = todo[at : at + PER_EVENT]
            log.append(
                REPAIR_APPLIED_KIND,
                RID,
                {
                    "repair": RID,
                    "since": running_version(),
                    "version": running_version(),
                    "findings": [i.key for i in part],
                    "details": [i.detail[:240] for i in part],
                    "checks": sorted({i.check for i in part}),
                    "reason": reason.strip(),
                    "note": note_.strip(),
                    "by": _who(),
                    "events": 0,
                    "summary": f"accepted history: {len(part)} finding(s)",
                },
            )
    return todo


def _who() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""
