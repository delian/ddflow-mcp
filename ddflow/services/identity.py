"""Carrying this clone's leases across the B190 identity upgrade.

B190 gave derived ids a per-clone suffix: `{host}-{tree}` became `{host}-{tree}-{hex6}`.
A lease claimed before that kept the bare id as its holder, and every holder comparison
afterwards -- renew, claim, complete, merge, the gate lease-keeper, the scheduler -- read
it as a stranger's: heartbeat said "no lease held" and a re-claim was refused as held by
someone else, on the agent's own work.

Rather than teach eleven comparisons an alias, the lease MOVES, once, on the record: a
release by the bare id and an acquisition by the suffixed one, carrying the claim's
worktree, branch, globs and resources. Release first: an acquisition over a live lease
of another holder is a contest (B191), which is exactly what this is not.

Only a lease the bare id acquired BEFORE this clone had a suffix is moved. From that
moment this clone derives the suffixed id, so a bare-id claim made later came from a
clone still on the bare id -- the very collision B190 ended -- and is not ours.
"""

from __future__ import annotations

import time

from ..config import Config
from ..core.model import State, fold
from ..infra.log import EventLog, bare_agent_id, clone_suffix_since


def _pre_upgrade(cfg: Config, st: State, bare: str, since: float) -> list[str]:
    live = st.active_leases(time.time(), cfg.lease.grace_s)
    return sorted(
        i
        for i, lease in live.items()
        if lease.holder == bare
        and lease.acquired_at < since
        and not st.items[i].removed
        and not st.items[i].lease_contest
    )


def rehome_pre_upgrade_leases(log: EventLog, cfg: Config, st: State, layer: str) -> list[str]:
    """Move this clone's pre-upgrade leases to its suffixed id. Returns the item ids moved.

    Only for a DERIVED identity: an explicit, env or config name is never suffixed, so
    it has no bare predecessor. Checked against the fold the caller already has, so the
    common case -- nothing to move -- costs one pass over the live leases and no lock.
    """
    if layer != "derived":
        return []
    me = log.agent_id
    bare = bare_agent_id(log.root)
    since = clone_suffix_since(log.root)
    if me == bare or not since or not _pre_upgrade(cfg, st, bare, since):
        return []
    with log.transaction():
        # Decided again under the lock: a sibling process may have moved them already.
        st = fold(log.read_all(), strict=False)
        moved = _pre_upgrade(cfg, st, bare, since)
        now = time.time()
        for item_id in moved:
            it = st.items[item_id]
            lease = it.lease
            assert lease is not None
            why = f"re-homed from {bare} to {me}: the B190 per-clone id upgrade"
            log.append(
                "lease.released",
                item_id,
                {"holder": bare, "event": lease.event, "by": me, "note": why},
            )
            log.append(
                "lease.acquired",
                item_id,
                {
                    "holder": me,
                    "at": now,
                    "ttl_s": lease.ttl_s,
                    "globs": list(lease.globs),
                    "worktree": lease.worktree,
                    "branch": lease.branch,
                    "note": f"{lease.note} [{why}]" if lease.note else why,
                    "kind": it.kind,
                    "resources": list(lease.resources),
                },
            )
    return moved
