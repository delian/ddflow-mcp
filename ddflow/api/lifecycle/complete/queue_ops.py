"""`remove`, `block`, `unblock`: take an item out of the queue, park it, release it.

Part of `ddflow.api.lifecycle.complete`."""

from __future__ import annotations

from pathlib import Path

from ....core import outcome as O
from ....core.model import ABANDONED, BLOCKED, DONE
from ....core.stuck import stuck_edges_to
from ....services import leases as L
from ..._base import _load_fresh
from .._common import _require
from .dispose import _dispose


def remove(
    repo: Path,
    item: str,
    *,
    reason: str = "",
    force: bool = False,
    agent: str = "",
    called_from: Path | None = None,
    shell_cwd: Path | None = None,
) -> O.Outcome:
    """Take an item out of the queue entirely.

    The event log is append-only, so this RECORDS a removal rather than deleting anything:
    the item and everything that happened to it stay in the history and in `ddflow
    replay`, which is what keeps the record honest about work that was planned and then
    dropped.
    """
    log, cfg, st = _load_fresh(repo, agent)
    it = _require(st, item, "item.removed", cfg.lease.grace_s, log.clone_id(create=False))
    if isinstance(it, O.Outcome):
        return it
    # Any item with work beneath it, not just a phase. The `kind == "phase"` guard
    # predates sub-tasks: removing a task umbrella left its children live but unreachable,
    # because `State.tasks(phase)` walks `descendants()` and `children()` skips a removed
    # node -- so the phase view reported "nothing actionable" while two open tasks sat
    # under the hole.
    kids = [t.id for t in st.open_descendants(item)]
    if kids and not force:
        return O.refused(
            "item.removed",
            f"{item} still has {len(kids)} task(s): {', '.join(kids[:8])}.\n"
            f"Remove them first, or --force to orphan them.",
            id=item,
            children=kids,
        )
    dependents = [o.id for o in st.items.values() if not o.removed and item in o.needs]
    if dependents and not force:
        return O.refused(
            "item.removed",
            f"{', '.join(dependents)} depend{'s' if len(dependents) == 1 else ''} on "
            f"{item}. Removing it would leave them blocked on something that no longer "
            f"exists (unknown dependencies are treated as unmet, deliberately).\n"
            f"Update them first, or --force.",
            id=item,
            dependents=dependents,
        )
    stranded = stuck_edges_to(st, item)
    if it.lease:
        L.release(log, item, note="removed from the queue")
    log.append(f"{it.family}.removed", item, {"reason": reason})
    return O.ok(
        "item.removed",
        id=item,
        children=[],
        dependents=[],
        stranded=[i for i, _ in stranded],
        drop_edges=[c for _, c in stranded],
        **_dispose(repo, cfg, log, item, (called_from, shell_cwd)),
    )


def block(
    repo: Path, item: str, *, reason: str = "", reopen: bool = False, agent: str = ""
) -> O.Outcome:
    """Park an item on something outside the queue — a vendor, an operator decision.

    The existence check is not ceremony. `fold`'s `_h_state` reaches items through
    `_item()`, which CREATES one when the id is unknown, so this was the only mutating
    command where a typo'd id materialised a titleless phantom task — which the scheduler
    then offered to an agent as the next thing to do.

    A DONE or ABANDONED item is refused (exit 3) unless `reopen`: blocking it moves finished work
    out of done, which a mistyped id would do silently (B-block-done). `reopen` is the
    deliberate form -- the B12/B14 data fix was one.
    """
    log, cfg, st = _load_fresh(repo, agent)
    it = _require(st, item, "item.blocked", cfg.lease.grace_s, log.clone_id(create=False))
    if isinstance(it, O.Outcome):
        return it
    if it.state in (DONE, ABANDONED) and not reopen:
        if it.state == DONE:
            why = (
                f"{item} is DONE; blocking it would move finished work out of done (and "
                f"anything depending on it would wait again)."
            )
        else:
            why = (
                f"{item} is ABANDONED; blocking it would revive work that was dropped on "
                f"purpose (a mistyped id would do it silently)."
            )
        return O.refused(
            "item.blocked",
            f"{why} Pass --reopen (reopen=true over MCP) if that is the intent.",
            id=item,
        )
    log.append("item.blocked", item, {"reason": reason})
    return O.Outcome(kind="item.blocked", data={"id": item, "reason": reason})


def unblock(repo: Path, item: str, *, note: str = "", agent: str = "") -> O.Outcome:
    """Release a blocked item -- and every blocked item beneath it -- back into the queue.

    The subtree is what makes "drive this legacy section" one command. An import holds
    the open work of an archive file as blocked, one phase per section, and naming the
    section is how its work becomes work again (the source project's `phase.py next
    --session X`). Releasing thirty tasks one id at a time is how half of them stay held.

    Exit 2 when nothing under `item` is blocked: "nothing to release" is a fact the
    caller should see, not a success that wrote no event.
    """

    log, cfg, st = _load_fresh(repo, agent)
    it = _require(st, item, "item.unblocked", cfg.lease.grace_s, log.clone_id(create=False))
    if isinstance(it, O.Outcome):
        return it
    targets = [item] if it.state == BLOCKED else []
    targets += sorted(
        d for d in st.descendants(item) if st.items[d].state == BLOCKED and not st.items[d].removed
    )
    if not targets:
        return O.nothing(
            "item.unblocked",
            f"{item} is {it.state} and nothing beneath it is blocked",
            id=item,
            was="",
            released=[],
        )
    was = it.blocked_reason if it.state == BLOCKED else ""
    for t in targets:
        log.append("item.unblocked", t, {"note": note, "was": st.items[t].blocked_reason})
    return O.ok("item.unblocked", id=item, was=was, released=targets)
