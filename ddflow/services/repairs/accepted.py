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
from ..approvals import consume as CN
from ..approvals.canon import Canon
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


def _fresh(log: EventLog) -> list[Item]:
    return [*mismatches(log.read_all()), *regressions(log)]


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


def pending_names(only: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in only.split(",") if x.strip())


def gate(
    ctx: Context, agent: str, reason: str, only: str = "", approval: str = ""
) -> tuple[str, dict[str, Any]]:
    """`(why not, approval stamp)`: why this call may not accept history ("" when it may).

    A call with no reason is refused. A PERSON at a terminal accepts directly. An agent (or the
    MCP surface) may not accept on its own word: it files `--approval request`, the person answers
    in chat, and the agent re-runs it with the approval id; the approval binds the findings
    accepted, so a changed or extra finding needs a new one (the agent can never approve itself).
    """
    if not reason.strip():
        return "--accept-history needs --history-reason: say why it is known", {}
    who = agent_marker(agent)
    if not who:
        return "", {}
    names = pending_names(only)
    keys = sorted(i.key for i in _fresh(ctx.log) if not names or i.ref in names)
    canon = Canon(
        verb="history.accept",
        agent=ctx.log.agent_id,
        item=",".join(names),
        args={"count": len(keys)},
        state=D.content_digest("\n".join(keys)),
    )
    decision = CN.operator_gate(
        ctx.log, ctx.cfg, canon, approval_id=approval, reason=reason, auto=False
    )
    if decision.ok:
        # The approval binds the EXACT findings it named: an agent may accept only those keys,
        # so an extra finding that appears after the person answered is never accepted on their
        # word (the stamp is passed through to `accept`, never recomputed there).
        stamp = dict(decision.stamp or {})
        stamp.setdefault("keys", keys)
        return "", stamp
    return (
        f"accepting history needs a person ({who}): re-run with `--approval request` "
        f"(MCP: approval=request) and the person answers in chat; or run it yourself in your own "
        f"terminal. {decision.message}",
        {},
    )


def accepting(
    ctx: Context,
    reason: str,
    note_: str = "",
    only: str = "",
    approved: frozenset[str] | None = None,
    by: str = "",
) -> Context:
    """`accept` for the names in ``only`` (comma-separated), then a fresh read of the log.

    ``approved`` is the set of finding keys the person's approval covers (an agent may accept
    nothing else); ``None`` means the caller is the person, who accepts what ``doctor`` just
    saw. An agent whose approval bound zero findings passes an empty set and accepts nothing.
    """
    accept(ctx, reason=reason, note_=note_, only=pending_names(only), approved=approved, by=by)
    return context(ctx.repo, ctx.log, ctx.cfg)


def accept(
    ctx: Context,
    *,
    reason: str,
    note_: str = "",
    only: tuple[str, ...] = (),
    approved: frozenset[str] | None = None,
    by: str = "",
) -> list[Item]:
    """Record the findings now open as accepted (those named by ``only``, if given); returns
    them. A non-``None`` ``approved`` set bounds what may be accepted (an agent accepts only
    the findings its approval names; ``None`` is the person, who accepts what is now open).
    Appends `repair.applied` events and nothing else; no line is edited."""
    log = ctx.log
    with log.transaction():
        fresh = _fresh(log)
        done = records(ctx.st)
        todo = [i for i in fresh if i.key not in done and (not only or i.ref in only)]
        if approved is not None:
            todo = [i for i in todo if i.key in approved]
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
                    # The person who accepted: for an agent's call this is the one who
                    # answered the approval, not the OS user that happens to run the process.
                    "by": (by.strip() or _who()),
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
