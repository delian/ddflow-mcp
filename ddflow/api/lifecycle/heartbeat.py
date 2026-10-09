"""`heartbeat` and `release`: keeping a lease alive and giving it back.

Part of `ddflow.api.lifecycle`, which re-exports every name defined here."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...core.model import fold
from ...services import eventcommit as EC
from ...services import leases as L
from ...services import waits as WT
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
    renewed = L.renew(log, item)
    hint = ""
    if not renewed:
        it = st.items.get(item)
        lease = it.lease if it else None
        if lease and _in_leased_tree(repo, lease.worktree, _tree_of(called_from or repo)):
            # Only a LIVE lease. Speaking for an expired one would resurrect a claim its
            # holder abandoned, and lock out whoever `recover` sent to take it over.
            if not lease.live(time.time(), cfg.lease.grace_s):
                hint = f"; its lease (held by {lease.holder}) has expired -- `claim {item}` again"
            else:
                renewed = L.renew(log, item, holder=lease.holder)
    if renewed:
        withheld = _catch_up_globs(log, cfg, item)
        return O.ok(
            "lease.renewed",
            id=item,
            renewed=True,
            waiters=_waiters(repo, item),
            globs_withheld=withheld,
            new_reports=_new_report_count(st, item),
        )
    return O.nothing(
        "lease.renewed",
        f"no lease held {item}{hint}",
        id=item,
        renewed=False,
        waiters=[],
        globs_withheld="",
    )


def _catch_up_globs(log, cfg, item: str) -> str:
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
    """

    why: list[str] = []
    for field in ("globs", "resources"):
        with log.transaction():
            it = fold(log.read_all(), strict=False).items.get(item)
            lease = it.lease if it else None
            # Cleared to none is caught up too: a revived lease left on the old values
            # would keep holding them against every other agent.
            if lease is None or sorted(getattr(lease, field)) == sorted(getattr(it, field)):
                continue
            new = list(getattr(it, field))
            args = (new, None) if field == "globs" else (None, new)
            live, refusal = L.plan_retarget(log, cfg, item, *args)
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


def _commit_events(log, cfg, reason: str) -> dict[str, str]:
    """`[log].commit_events`: commit the event shards when an item is completed or handed
    back, so the log in git is the whole log (Bcd3512c891). Not at merge: `complete`
    follows it, and the base's head is then still the merge commit `merge` reported.
    Never fails the caller: a refusal is returned."""
    if not cfg.log.commit_events:
        return {}

    sha, why = EC.commit_shards(log.root, reason)
    if sha:
        return {"events_committed": sha}
    return {"events_note": why} if why else {}


def release(repo: Path, item: str, *, note: str = "", agent: str = "") -> O.Outcome:
    log, cfg, _st = _load(repo, agent)
    # Read BEFORE letting go: a waiter wakes on the release and unregisters, and one
    # quick enough to do that before a read after it would never be reported.
    waiting = _waiters(repo, item)
    released = L.release(log, item, note=note)
    if released:
        committed = _commit_events(log, cfg, f"release {item}")
        return O.ok("lease.released", id=item, released=True, woke=waiting, **committed)
    return O.nothing("lease.released", f"no lease on {item}", id=item, released=False, woke=[])
