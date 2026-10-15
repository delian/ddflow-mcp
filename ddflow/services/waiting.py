"""Who waits for whom: `lease.waiting` records and the wait-for graph they make.

Hold-and-wait is the textbook deadlock condition, and ddflow invited it: `update --globs`
refused a glob another lease held, the agent KEPT its current lease and polled, and the
holder of the wanted glob did the same in the other direction (three agents, 45 minutes,
2026-10-15). A refused claim or widening now returns at once and leaves a record here:

* ``register`` appends a `lease.waiting` event (who, which globs, since when, behind which
  leases, and a lifetime tied to the waiter's lease). It is a normal logged kind: it folds
  into `State.waiting`, replays, and an older ddflow preserves it.
* ``standing`` says which records still hold NOW: the waiter's lease unchanged and live and
  the wanted globs not yet its own (a claim wait has no lease: it lasts ``ttl_s``).
* ``wait_for_graph`` is the data the cycle detector, the breakable lease and the auto-order
  read: waiter item -> the items whose live leases overlap what it asked for, recomputed
  against the leases standing now, so an edge vanishes the moment its holder lets go.
* ``release_advice`` is the end of the wait: once a wait is older than `[lease].wait_max_s`,
  the waiter is told which of ITS OWN globs other agents are waiting for. It releases them
  (`update --globs` without them) or reports a cycle. Nothing is evicted.
* ``queue_ahead`` is the optional FIFO (`--queue`, `[lease].wait_queue`): a later request for
  files an earlier queued waiter asked for is refused as queued behind it.

Pure reads over a folded `State`; ``register`` is the only writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.admission import glob_conflict
from ..core.globs import overlap as globs_overlap


def _live(state, cfg, now: float):
    return state.active_leases(now, cfg.lease.grace_s)


def blockers(
    state, cfg, item_id: str, holder: str, globs: list[str], now: float
) -> list[dict[str, Any]]:
    """Every live lease of ANOTHER agent that ``globs`` overlap, or that holds ``item_id``
    itself: ``[{"item", "holder", "pair"}]`` in the order found."""
    it = state.items.get(item_id)
    if it is None:
        return []
    live = dict(_live(state, cfg, now))
    out: list[dict[str, Any]] = []
    own = live.get(item_id)
    if own is not None and own.holder != holder:
        out.append({"item": item_id, "holder": own.holder, "pair": ["", ""]})
    while True:
        live.pop(item_id, None)
        clash = glob_conflict(state, cfg, it, globs, holder, against="live", live=live)
        if clash is None:
            return out
        out.append(
            {"item": clash.item, "holder": clash.lease.holder, "pair": list(clash.pair)}
            if clash.lease
            else {"item": clash.item, "holder": "", "pair": list(clash.pair)}
        )
        live.pop(clash.item, None)


def standing(state, cfg, now: float) -> dict[str, dict[str, Any]]:
    """Item id -> its `lease.waiting` record, for the records that still hold at ``now``."""
    out: dict[str, dict[str, Any]] = {}
    for item_id, w in state.waiting.items():
        it = state.items.get(item_id)
        if it is None or it.removed:
            continue
        lease = it.lease
        held = lease is not None and lease.live(now, cfg.lease.grace_s)
        if w["lease_event"]:
            # A widening wait lives and dies with the lease it was made under; once the
            # lease covers what was asked for, the wait is over.
            if not held or lease.event != w["lease_event"] or lease.holder != w["holder"]:
                continue
            if all(g in lease.globs for g in w["globs"]):
                continue
        else:
            if now > w["at"] + w["ttl_s"]:
                continue
            if held and lease.holder == w["holder"] and lease.acquired_at >= w["since"]:
                continue
        out[item_id] = w
    return out


def wait_for_graph(state, cfg, now: float) -> dict[str, list[str]]:
    """The wait-for graph: waiter item -> the items it waits for, sorted. Only waiters with
    at least one edge now appear (a wait whose holders let go is no longer a wait)."""
    graph: dict[str, list[str]] = {}
    for item_id, w in standing(state, cfg, now).items():
        edges = sorted(
            {b["item"] for b in blockers(state, cfg, item_id, w["holder"], w["globs"], now)}
        )
        if edges:
            graph[item_id] = edges
    return graph


@dataclass(frozen=True)
class Waiter:
    """One standing wait, with its edges as of now."""

    item: str
    holder: str
    globs: list[str]
    since: float
    queue: bool
    on: list[dict[str, Any]]


def waiters(state, cfg, now: float) -> list[Waiter]:
    """Every standing wait that still has an edge, oldest first (then by item id)."""
    out = []
    for item_id, w in standing(state, cfg, now).items():
        on = blockers(state, cfg, item_id, w["holder"], w["globs"], now)
        if on:
            out.append(Waiter(item_id, w["holder"], list(w["globs"]), w["since"], w["queue"], on))
    return sorted(out, key=lambda x: (x.since, x.item))


def waiting_for(state, cfg, item_id: str, now: float) -> list[dict[str, Any]]:
    """Who waits for ``item_id``'s lease, and which of ITS globs they asked for:
    ``[{"item", "holder", "since", "globs"}]``, oldest first."""
    it = state.items.get(item_id)
    lease = it.lease if it else None
    if lease is None or not lease.live(now, cfg.lease.grace_s):
        return []
    out = []
    for w in waiters(state, cfg, now):
        if w.holder == lease.holder or not any(b["item"] == item_id for b in w.on):
            continue
        mine = [g for g in lease.globs if any(globs_overlap(g, x) for x in w.globs)]
        out.append({"item": w.item, "holder": w.holder, "since": w.since, "globs": mine})
    return out


def release_advice(state, cfg, item_id: str, now: float) -> dict[str, Any] | None:
    """What an over-long waiter must do: ``{"waited_s", "release", "waiters"}`` when
    ``item_id`` has waited longer than `[lease].wait_max_s` while its own lease is wanted
    by others (``release``: the own globs they asked for), else None. 0 turns it off."""
    limit = cfg.lease.wait_max_s
    w = standing(state, cfg, now).get(item_id)
    if limit <= 0 or w is None or now - w["since"] < limit:
        return None
    asked = waiting_for(state, cfg, item_id, now)
    release = sorted({g for a in asked for g in a["globs"]})
    if not release:
        return None
    return {"waited_s": now - w["since"], "release": release, "waiters": asked}


def advice_text(item_id: str, advice: dict[str, Any], keep: list[str]) -> str:
    """The sentence a refusal appends when the wait ran past `[lease].wait_max_s`."""
    who = ", ".join(f"{a['holder']} ({a['item']})" for a in advice["waiters"])
    left = ",".join(g for g in keep if g not in advice["release"]) or "<the globs you keep>"
    return (
        f"\n\n{item_id} has waited {advice['waited_s']:.0f}s, and {who} wait for files YOU hold: "
        f"{', '.join(advice['release'])}. Do not keep holding them while you wait: release "
        f"them (`ddflow update {item_id} --globs {left}`) or report a cycle to the "
        f"coordinator. Nothing is taken from you."
    )


def queue_ahead(
    state, cfg, item_id: str, holder: str, globs: list[str], now: float
) -> Waiter | None:
    """The earliest standing QUEUED waiter, not ``holder``, that asked for files ``globs``
    overlap and asked first -- ahead of ``item_id`` in the FIFO -- or None."""
    held = standing(state, cfg, now)
    mine = held.get(item_id)
    cutoff = mine["since"] if mine else float("inf")
    for other, w in sorted(held.items(), key=lambda kv: (kv[1]["since"], kv[0])):
        if not w["queue"] or w["holder"] == holder or other == item_id or w["since"] >= cutoff:
            continue
        if not any(globs_overlap(a, b) for a in globs for b in w["globs"]):
            continue
        # Its place is kept while its files are held, and for `waiter_reservation_s` after
        # they free up (the time it needs to come back and take them).
        edges = blockers(state, cfg, other, w["holder"], w["globs"], now)
        if edges or now - w["at"] <= cfg.lease.waiter_reservation_s:
            return Waiter(other, w["holder"], list(w["globs"]), w["since"], True, edges)
    return None


def _refresh_s(cfg) -> float:
    """How old a record may grow before asking again re-appends it, so ``at`` (the last
    ask, which a queued place is measured from) stays within half the place's lifetime."""
    return (cfg.lease.waiter_reservation_s or cfg.lease.ttl_s) / 2


def register(
    log,
    cfg,
    state,
    item_id: str,
    holder: str,
    globs: list[str],
    now: float,
    *,
    queue: bool = False,
    lease=None,
) -> dict[str, Any] | None:
    """Record that ``holder`` asked for ``globs`` on ``item_id`` and was refused.

    ``lease`` is the lease a widening was made under (None for a claim). The wait keeps its
    ``since`` while it is the same request, and nothing is appended when nothing changed, so
    an agent that asks again every minute does not fill the log. Returns the record, or None
    when no other lease stands in the way. Call under the log's transaction."""
    on = blockers(state, cfg, item_id, holder, globs, now)
    if not on:
        return None
    queue = bool(queue or cfg.lease.wait_queue)
    lease_event = lease.event if lease is not None else ""
    prior = standing(state, cfg, now).get(item_id)
    same = (
        prior is not None
        and prior["holder"] == holder
        and prior["lease_event"] == lease_event
        and sorted(prior["globs"]) == sorted(globs)
    )
    since = prior["since"] if same else now
    row = {
        "holder": holder,
        "globs": list(globs),
        "on": on,
        "since": since,
        "queue": queue,
        "ttl_s": cfg.lease.ttl_s,
        "lease_event": lease_event,
        "at": now,
    }
    if (
        same
        and prior["queue"] == queue
        and [o["item"] for o in prior["on"]] == [o["item"] for o in on]
        and now - prior["at"] < _refresh_s(cfg)
    ):
        return prior
    log.append("lease.waiting", item_id, row)
    return row


def rows(state, cfg, now: float) -> list[dict[str, Any]]:
    """Every standing wait as `status`, `brief` and `lanes` show it, oldest first:
    ``{"item", "agent", "since", "waited_s", "globs", "waiting_on": [{"item", "holder"}],
    "queue"}``."""
    return [
        {
            "item": w.item,
            "agent": w.holder,
            "since": w.since,
            "waited_s": max(0, int(now - w.since)),
            "globs": list(w.globs),
            "waiting_on": [{"item": o["item"], "holder": o["holder"]} for o in w.on],
            "queue": w.queue,
        }
        for w in waiters(state, cfg, now)
    ]


def line(r: dict[str, Any]) -> str:
    """One wait as a sentence: who waits for whom, how long, and for which files."""
    on = ", ".join(f"{o['item']} ({o['holder']})" for o in r["waiting_on"])
    q = " [queued]" if r["queue"] else ""
    return (
        f"{r['agent']} on {r['item']} waits {r['waited_s']}s for {on}: {', '.join(r['globs'])}{q}"
    )
