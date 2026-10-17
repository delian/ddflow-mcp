"""`heartbeat` and `release`: keeping a lease alive and giving it back.

Part of `ddflow.api.lifecycle`, which re-exports the public names defined here."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...core.model import fold
from ...services import eventcommit as EC
from ...services import leasebreak as LB
from ...services import leases as L
from ...services import waits as WT
from ...services.cleanup import stranded_text
from ...services.leaseclock import localize
from .._base import _load
from .claim import _in_leased_tree, _new_report_count, _tree_of


def heartbeat(
    repo: Path, item: str, *, agent: str = "", called_from: Path | None = None
) -> O.Outcome:
    """Renew a lease: one I hold, or the one that made the tree I am standing in.

    Identity is derived from the tree, so an agent working in the tree `claim` made for
    an item is usually NOT the identity that claimed it -- and was told "no lease held"
    by the one command it runs from where it works. `check_commit` already counts the
    lease that created this tree as mine; so does this. It renews on behalf of the
    holder, never taking the lease over, and standing in some OTHER tree renews nothing.
    """
    log, cfg, st = _load(repo, agent)
    if not item:
        item, missing = _the_held_item(repo, st, log.agent_id, called_from)
        if missing is not None:
            return missing
    gone = st.items.get(item)
    if (
        gone is not None
        and gone.removed
        and gone.lease
        and gone.lease.live(time.time(), cfg.lease.grace_s)
    ):
        # Renewing would keep a lease on an item nobody can finish (B71dedb76d2).
        ls = gone.lease
        return O.refused(
            "lease.renewed",
            stranded_text(item, ls.holder, ls.branch or gone.branch, ls.worktree),
            id=item,
            renewed=False,
            waiters=[],
            globs_withheld="",
            yield_requested="",
        )
    began = time.time()  # before the renewal: the lease is live at this instant once renewed
    renewed = L.renew(log, item)
    holder = log.agent_id  # whose lease the catch-up may point at the item
    hint = ""
    if not renewed:
        it = st.items.get(item)
        lease = it.lease if it else None
        if lease and _in_leased_tree(repo, lease.worktree, _tree_of(called_from or repo)):
            # Only a LIVE lease. Speaking for an expired one would resurrect a claim its
            # holder abandoned, and lock out whoever `recover` sent to take it over.
            if not lease.live(time.time(), cfg.lease.grace_s):
                hint = f"; its lease (held by {lease.holder}) has expired -- `claim {item}` again as {lease.holder}"
            else:
                renewed = L.renew(log, item, holder=lease.holder)
                holder = lease.holder
    if not renewed:
        broken = [n for n in LB.broken_notices(log.read_all(), log.agent_id) if n["item"] == item]
        if broken:  # the lease was taken from this agent: say so wherever it stands
            hint = "; " + LB.notice_text(broken[0])
    if renewed:
        withheld = _catch_up_globs(log, cfg, item, began, holder)
        return O.ok(
            "lease.renewed",
            id=item,
            renewed=True,
            waiters=_waiters(repo, item),
            globs_withheld=withheld,
            new_reports=_new_report_count(st, item),
            yield_requested=_yield_request(log, cfg, item),
        )
    return O.nothing(
        "lease.renewed",
        f"no lease held {item}{hint}",
        id=item,
        renewed=False,
        waiters=[],
        globs_withheld="",
        yield_requested="",
    )


def _the_held_item(repo: Path, st, me: str, called_from) -> tuple[str, O.Outcome | None]:
    """The item to renew when none was named: the only one ``me`` holds (or whose tree this
    is), else the refusal that names the missing id and lists what is held."""
    here = _tree_of(called_from or repo)
    leased = [(i, it.lease) for i, it in st.items.items() if it.lease and not it.removed]
    held = sorted(i for i, ls in leased if ls.holder == me)
    whose = f"{me} holds"
    if not held:
        # The lease that made the tree I stand in is mine to renew: identity is the tree's.
        held = sorted(i for i, ls in leased if _in_leased_tree(repo, ls.worktree, here))
        whose = "this tree carries"
    if len(held) == 1:
        return held[0], None
    base = {"renewed": False, "waiters": [], "globs_withheld": "", "yield_requested": ""}
    if not held:
        return "", O.nothing(
            "lease.renewed",
            f"heartbeat needs an item id (`ddflow heartbeat <id>`), and {me} holds no lease.",
            **base,
        )
    return "", O.failed(
        "lease.renewed",
        f"heartbeat needs an item id: {whose} {len(held)} items -- "
        + ", ".join(f"`ddflow heartbeat {i}`" for i in held),
        **base,
    )


def _yield_request(log, cfg, item: str) -> str:
    """Why this holder is asked to yield ("" when it is not): another item waits for its
    files and it has shown no progress for `[lease].idle_max_s` (`services.leasebreak`)."""
    now = time.time()
    st = localize(fold(log.read_all(), strict=False), log, now)
    for v in LB.verdicts(st, cfg, log.read_all(), now, LB.tree_activity(log.root)):
        if v.item == item:
            return LB.yield_text(v)
    return ""


def _catch_up_globs(log, cfg, item: str, now: float, holder: str) -> str:
    """Point a just-renewed lease at the item's CURRENT globs and resources; "" or why
    it could not.

    `update --globs` retargets a LIVE lease and leaves a lapsed one for recovery, so an
    edit made while the lease had lapsed reached only the item. The heartbeat that then
    revived the lease kept its claim-time globs, and the commit hook reported the paths
    just added as unleased (Bb21d338f26). Now that the lease is live again it takes the
    item's globs -- through `plan_retarget`, the same overlap check as any widening, so
    paths another agent took in the meantime are withheld and the holder is told.
    Resources the same way, through the capacity check (B7f8060f2f5); each is caught up
    on its own, so a refusal of one does not hold back the other. A claim records its
    globs and resources on the item, so this never undoes the claim itself.

    ``now`` is when the renewal began. The revived lease keeps the TTL it was claimed with,
    so a process that took longer than that between renewing and catching up (a loaded
    machine, a short TTL) judged it lapsed again and silently caught up nothing
    (B8eb33fe297); the catch-up is judged at the renewal instead -- and only for the lease
    ``holder`` renewed: one that changed hands in that window (a takeover, a break) is not
    pointed at the item's values, which were never the new holder's.
    """

    why: list[str] = []
    for field in ("globs", "resources"):
        with log.transaction():
            it = localize(fold(log.read_all(), strict=False), log, now).items.get(item)
            lease = it.lease if it else None
            # Cleared to none is caught up too: a revived lease left on the old values
            # would keep holding them against every other agent.
            if lease is None or lease.holder != holder:
                continue
            if sorted(getattr(lease, field)) == sorted(getattr(it, field)):
                continue
            new = list(getattr(it, field))
            args = (new, None) if field == "globs" else (None, new)
            live, refusal = L.plan_retarget(log, cfg, item, *args, now=now)
            if refusal:
                why.append(refusal)
            elif live is not None:
                L.retarget(log, item, live, *args)
    return "; ".join(why)


def _waiters(repo: Path, item: str) -> list[dict[str, Any]]:
    """Who is blocked on `item` right now, from `ddflow wait` registrations.

    The holder's side of waking: at a heartbeat it is a reason to finish, narrow the
    globs, or release early; at a release or completion it is the list of agents this
    just woke. Advisory -- a registry that cannot be read is simply no waiters.
    """

    try:
        return WT.waiting_on(repo, item)
    except (OSError, ValueError, TypeError, AttributeError):
        return []  # advisory: a waiter's bad file must never stop a holder releasing


def _commit_events(log, cfg, reason: str, door: str = "complete") -> dict[str, str]:
    """`[log].commit_events` at a door (Bcd3512c891): see `eventcommit.commit_at`."""
    return EC.commit_at(log, cfg, door, reason)


def release(
    repo: Path,
    item: str,
    *,
    note: str = "",
    agent: str = "",
    force: bool = False,
    reason: str = "",
) -> O.Outcome:
    """Give up a lease. Another CLONE's live lease is not released without ``force`` and a
    ``reason``, recorded on the release (B90b43dd2d8)."""
    log, cfg, _st = _load(repo, agent)
    refusal, forced = L.foreign_guard(
        log, cfg, item, force=force, reason=reason, what="releasing it"
    )
    if refusal:
        return O.refused("lease.released", refusal, id=item, released=False, woke=[])
    note = "; ".join(filter(None, [forced, note]))
    # Read BEFORE letting go: a waiter wakes on the release and unregisters, and one
    # quick enough to do that before a read after it would never be reported.
    waiting = _waiters(repo, item)
    released = L.release(log, item, note=note, forced=bool(forced))
    if released:
        committed = _commit_events(log, cfg, f"release {item}", "release")
        return O.ok("lease.released", id=item, released=True, woke=waiting, **committed)
    return O.nothing("lease.released", f"no lease on {item}", id=item, released=False, woke=[])
