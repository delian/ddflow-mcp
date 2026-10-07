"""`wait`: sleep on the event log until an item (or anything) can be claimed.

Part of `ddflow.api.lifecycle`, which re-exports every name defined here."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...core import globspec as GS
from ...core import outcome as O
from ...core.model import ABANDONED, DONE, REVIEW
from ...core.plain import plain
from .._base import _load
from ._common import _require
from .ready import DEFAULT_NEXT_KIND
from .reservations import (
    WAITABLE,
    _blocking_leases,
    _claim_blocker,
    _clears_on_release,
    _in_motion,
    _reservation_hold,
)

#: How long `wait` blocks when the caller does not say. Long enough to outlast most
#: holders' remaining work, short enough that a forgotten wait does not hold a process
#: for an afternoon. A caller that wants longer asks again, which also re-checks that
#: waiting is still the right move.
DEFAULT_WAIT_TIMEOUT_S = 600


def _judge_wait(
    st,
    cfg,
    me: str,
    item: str,
    phase: str,
    kind: str,
    globs: list[str] | None = None,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Can the caller start work NOW, must it wait, or would waiting never end?

    Returns {"status": "ready"|"blocked"|"hopeless", "why", "waiting_on", "ready",
    "blocked"}. "hopeless" is the case that matters most: a wait that no other agent's
    progress can end is a stall dressed as patience, so it is refused up front with the
    move that WOULD help.
    """
    now = time.time()
    live = st.active_leases(now, cfg.lease.grace_s)
    others = {i: lz for i, lz in live.items() if lz.holder != me}
    if not item:
        return _judge_any(st, cfg, me, phase, kind, now, live, repo)
    it = st.items.get(item)
    out: dict[str, Any] = {"why": "", "waiting_on": [], "ready": [], "blocked": []}
    if it is None or it.removed:
        return {**out, "status": "hopeless", "why": f"{item} was removed from the queue"}
    if it.state in (DONE, ABANDONED):
        return {**out, "status": "hopeless", "why": f"{item} is already {it.state}"}
    if it.state == REVIEW:
        return {
            **out,
            "status": "hopeless",
            "why": f"{item} is in review: its reviewers have it, not an agent. "
            f"`ddflow pr sync` brings it back if they request changes.",
        }
    mine = live.get(item)
    if mine is not None and mine.holder == me:
        return {**out, "status": "ready", "why": f"you already hold {item}", "ready": [item]}
    b = _claim_blocker(st, cfg, it, me, live, now, globs=globs, repo=repo)
    if b is None:
        return {**out, "status": "ready", "why": f"{item} is free to claim", "ready": [item]}
    out["blocked"] = [plain(b)]
    if b.reason == "expired":
        return {**out, "status": "hopeless", "why": b.detail}
    # No "cap reached" blocker arrives here: the parallelism cap trims `plan`'s ready
    # LIST and `claim` does not apply it to a named item, so `_claim_blocker` never
    # returns it (pinned by test_a_cap_blocked_item_wait_agrees_with_claim). Only the
    # any-wait meets the cap, in `_blocking_leases`.
    if b.reason not in WAITABLE:
        return {
            **out,
            "status": "hopeless",
            "why": f"{item}: {b.reason} — {b.detail}. That does not clear when another "
            f"agent finishes; someone has to act on it.",
        }
    if b.reason == "resources":
        theirs = sorted(i for i, lz in others.items() if lz.resources)
        if not theirs:
            held = sorted(i for i, lz in live.items() if lz.holder == me and lz.resources)
            return {
                **out,
                "status": "hopeless",
                "why": f"{item}: {b.detail}, and only "
                + (f"{', '.join(held)}, which YOU hold," if held else "nobody")
                + " holds that resource. Finish or release it first; waiting on "
                "yourself never ends.",
            }
        return {
            **out,
            "status": "blocked",
            "why": f"{item}: {b.reason} — {b.detail}",
            "waiting_on": theirs,
        }
    if b.reason == "conflict":
        return {
            **out,
            "status": "blocked",
            "why": f"{item}: {b.detail}",
            "waiting_on": b.waiting_on,
        }
    held_by_me = [d for d in b.waiting_on if d in live and live[d].holder == me]
    if held_by_me:
        return {
            **out,
            "status": "hopeless",
            "why": f"{item} waits on {', '.join(held_by_me)}, which YOU hold. "
            f"Finish that first; waiting on yourself never ends.",
        }
    moving = [d for d in b.waiting_on if _in_motion(st, d, others)]
    if not moving:
        return {
            **out,
            "status": "hopeless",
            "why": f"{item} waits on {', '.join(b.waiting_on) or 'its dependencies'}, "
            f"and nobody is working on them. Take one of them instead of waiting.",
        }
    return {**out, "status": "blocked", "why": f"{item}: deps — {b.detail}", "waiting_on": moving}


def _judge_any(
    st, cfg, me: str, phase: str, kind: str, now: float, live, repo: Path | None = None
) -> dict[str, Any]:
    """`_judge_wait` for "anything": the same ready set `next` offers."""
    from ...core.schedule import plan

    others = {i: lz for i, lz in live.items() if lz.holder != me}
    # The same offer `next` makes: what is reserved for a waiter in line is not ready.
    hold = _reservation_hold(repo, st, cfg, me, now) if repo is not None else None
    from ...services.flowstate import limit_for

    # The same limit `next` plans with, so a waiter waits on the offer `next` would make.
    parallel = None
    if repo is not None:
        from ...infra.log import EventLog

        # The log, read lazily as `next` reads it, so the rates are re-read here too.
        def events():
            return EventLog(repo, me, log_cfg=cfg.log).read_all()

        parallel = limit_for(repo, cfg, st, events)
    p = plan(st, cfg, kind=kind, phase=phase, now=now, agent=me, hold=hold, parallel=parallel)
    out: dict[str, Any] = {
        "why": "",
        "waiting_on": [],
        "ready": [],
        "blocked": [plain(b) for b in p.blocked],
    }
    # Only what `claim` would grant: under `ready_policy = deps_only` the plan offers
    # items another agent's lease or globs still cover.
    refused = [(i, _claim_blocker(st, cfg, i, me, live, now, repo=repo)) for i in p.ready]
    ready = [i.id for i, b in refused if b is None]
    if ready:
        return {**out, "status": "ready", "ready": ready}
    p.blocked.extend(b for _i, b in refused)
    out["blocked"] = [plain(b) for b in p.blocked]
    if not others:
        return {
            **out,
            "status": "hopeless",
            "why": f"nothing is ready and no other agent holds anything ({p.summary()}), "
            "so no release is coming to wake you. `ddflow next` says what blocks the "
            "queue — it needs someone to act, not to wait.",
        }
    by_item = {b.item: b for b in p.blocked}
    stuck = [b for b in p.blocked if not _clears_on_release(st, b, others, by_item)]
    if p.blocked and len(stuck) == len(p.blocked):
        # Every blocker needs a person: a cycle, an expired lease under "report", an
        # operator's block, a dependency nobody works on. Some other agent holding an
        # unrelated lease does not change that, and sleeping to the deadline only to
        # say "still blocked" hides the one thing to do (B02e99efc75).
        return {
            **out,
            "status": "hopeless",
            "why": "nothing is ready, and nothing blocked clears when another agent "
            "finishes -- each needs someone to act: "
            + "; ".join(f"{b.item}: {b.reason} — {b.detail}" for b in stuck),
        }
    blocking = _blocking_leases(st, p.blocked, others)
    return {
        **out,
        "status": "blocked",
        "why": f"nothing is ready ({p.summary()}); waiting on "
        + ", ".join(f"{i} ({others[i].holder})" for i in blocking),
        "waiting_on": blocking,
    }


def _freed(st, cfg, was: list[str], me: str) -> list[str]:
    """What happened to each item the caller was waiting on -- the answer to "why did
    I wake?", in the words an agent can relay."""
    now = time.time()
    live = st.active_leases(now, cfg.lease.grace_s)
    out: list[str] = []
    for i in was:
        it = st.items.get(i)
        if it is None:
            out.append(f"{i}: gone")
        elif it.state in (DONE, ABANDONED):
            out.append(f"{i}: {it.state}")
        elif i not in live:
            lz = it.lease
            out.append(f"{i}: lease {'expired' if lz else 'released'}")
        elif live[i].holder == me:
            out.append(f"{i}: now yours")
        else:
            out.append(f"{i}: still held by {live[i].holder}")
    return out


def _wait_globs(globs, item: str) -> tuple[list[str] | None, str]:
    """(the globs `wait` judges the item's claim on, or None for its stored ones; why
    they cannot be used, or "")."""
    want = GS.parse(globs) or None
    if want and not item:
        # The any-wait judges every item on its own globs; accepting these and dropping
        # them would answer READY for a claim they then refuse.
        return None, "globs need an item: they are the globs you will claim THAT item with"
    return want, GS.problem(want or [])


def wait(
    repo: Path,
    *,
    item: str = "",
    phase: str = "",
    kind: str = DEFAULT_NEXT_KIND,
    timeout_s: float | None = None,
    poll_s: float | None = None,
    agent: str = "",
    on_progress=None,
    globs: str | list[str] | None = None,
) -> O.Outcome:
    """Block until ``item`` (or, without one, anything) can be started. Exit 0 on wake.

    ``globs`` (with ``item``) are the globs the caller will claim with: READY then means
    that claim is not refused for them. Without them, the item's stored globs are used.

    Exit 2 when the deadline passes with it still blocked, and at once -- without
    sleeping -- when waiting cannot help: the item is done, in review, in a cycle, held
    by an operator, or waits on work nobody is doing. ``timeout_s=0`` asks the question
    without waiting at all.

    Waking is a hint, not a reservation: two agents waiting on one release both wake,
    and one of them loses the `claim`. The loser is refused with alternatives and can
    wait again. A queue that reserved on wake would need the waiter to be alive to use
    the reservation, which is the crash-recovery problem leases already solve.
    """
    from ...services import waits as WT

    timeout = DEFAULT_WAIT_TIMEOUT_S if timeout_s is None else float(timeout_s)
    poll = WT.POLL_S if poll_s is None else float(poll_s)
    empty: dict[str, Any] = {
        "item": item,
        "phase": phase,
        "woke": False,
        "waitable": False,
        "ready": [],
        "waiting_on": [],
        "freed_by": [],
        "blocked": [],
        "waited_s": 0,
    }
    if timeout < 0 or poll <= 0:
        return O.failed("wait", "timeout must be >= 0 and poll > 0 seconds", **empty)
    want, bad = _wait_globs(globs, item)
    if bad:
        return O.failed("wait", bad, **empty)
    log, cfg, st = _load(repo, agent)
    if item:
        found = _require(st, item, "wait")
        if isinstance(found, O.Outcome):
            return O.Outcome("wait", {**empty, **found.data}, found.exit, found.reason)
    me = cfg.agent.id or log.agent_id
    say = on_progress or (lambda _msg: None)

    def result(v: dict[str, Any], waited: float, freed: list[str]) -> O.Outcome:
        data = {
            **empty,
            "woke": v["status"] == "ready",
            "waitable": v["status"] != "hopeless",
            "ready": v["ready"],
            "waiting_on": v["waiting_on"],
            "freed_by": freed,
            "blocked": v["blocked"],
            "waited_s": round(waited),
        }
        if v["status"] == "ready":
            data["advice"] = (
                f"`ddflow claim {v['ready'][0]}` now: anyone else waiting on the same "
                f"release woke too."
            )
            return O.ok("wait", **data)
        if v["status"] == "hopeless":
            return O.nothing("wait", f"Not waiting: {v['why']}", **data)
        return O.nothing(
            "wait",
            f"Still blocked after {round(waited)}s: {v['why']}. `ddflow wait` again to "
            f"keep waiting.",
            **data,
        )

    v = _judge_wait(st, cfg, me, item, phase, kind, want, repo)
    if v["status"] != "blocked" or timeout == 0:
        return result(v, 0.0, [])

    started = time.monotonic()
    deadline = started + timeout
    # A place already held by a refused `claim` of this item is kept: queuing by `wait`
    # must not send an agent to the back of a line it has stood in for an hour.
    carried = WT.take_place(repo, me, item)  # a place a refused claim already holds
    w = WT.register(
        repo,
        WT.Waiter(
            agent=me,
            item=item,
            phase=phase,
            waiting_on=v["waiting_on"],
            reason=v["why"],
            since=carried,
            until=time.time() + timeout,
        ),
    )
    # Woken for an item, or carrying an older place: the place in line stays on at the end.
    keep = carried > 0
    WT.drop_queue(repo, me, item)  # the wait record is on disk: it carries the place now
    say(f"waiting (up to {round(timeout)}s): {v['why']}")
    # None, so the first pass re-judges whatever landed between the load above and
    # the registration -- a release in that gap must not cost a whole RECHECK_S.
    seen = None  # an EventLog.mark() once taken
    checked = started
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return result(v, time.monotonic() - started, [])
            time.sleep(min(poll, left))
            # Fingerprint BEFORE the read, never after: see `EventLog.mark`.
            ext = log.mark()
            if ext == seen and time.monotonic() - checked < WT.RECHECK_S:
                continue
            seen, checked = ext, time.monotonic()
            log, cfg, st = _load(repo, agent)
            was = v
            v = _judge_wait(st, cfg, me, item, phase, kind, want, repo)
            if v["status"] != "blocked":
                keep = v["status"] == "ready" and bool(item)
                freed = _freed(st, cfg, was["waiting_on"], me)
                if freed:
                    say("woke: " + "; ".join(freed))
                return result(v, time.monotonic() - started, freed)
            if v["why"] != was["why"]:
                say(f"still waiting: {v['why']}")
                WT.update(w, waiting_on=v["waiting_on"], reason=v["why"])
    finally:
        _end_wait(WT, w, keep, cfg)


def _end_wait(WT, w, keep: bool, cfg) -> None:
    """Leave the registry. A wait that WOKE for an item keeps its place in line: the wait
    ends when the item is claimable, and the CLAIM follows from another process; until it
    lands, this registration is the reservation (`_reserved_for`)."""
    if keep and cfg.lease.waiter_reservation_s > 0:
        WT.mark_woken(w, cfg.lease.waiter_reservation_s)
    else:
        WT.unregister(w)
