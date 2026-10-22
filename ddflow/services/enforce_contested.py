"""Paths a lease covers that another agent's live lease also covers (Bb7df2561a1).

`update --globs` (`leases.plan_retarget`) grants a glob over a wildcard another lease holds
when that lease's holder has changed nothing there, and ignores the leases of items that are
finished. The commit hook has to agree, or `update` hands out a claim the hook then ignores:
the wildcard's holder changes the very file the second claimer was granted, and no lease
names the clash. So a staged path a lease of the committer's covers ONLY by a wildcard is
refused when another live lease covers it as well and that lease's holder has changed it
(or git cannot say), or names it exactly.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import Config
from ..core.flow import line_key
from ..core.globs import inside as path_in_glob
from ..core.model import Lease, State
from ..core.schedule import is_shared, shared_globs
from ..infra.log import EventLog
from . import leases as L


def _wild(glob: str) -> bool:
    return any(c in glob for c in "*?[")


def contested(
    log: EventLog,
    cfg: Config,
    state: State,
    paths: list[str],
    mine: list[str],
    is_mine: Callable[[Lease], bool],
    now: float,
) -> dict[str, str]:
    """path -> "item (holder)" for each of ``paths`` the committer covers by wildcard only
    and another live, unfinished lease on the same line covers too, as `update` judges it."""
    shared = shared_globs(cfg)
    subject = [
        p
        for p in paths
        if not is_shared(p, shared)
        and any(path_in_glob(p, g) for g in mine)
        and all(_wild(g) for g in mine if path_in_glob(p, g))
    ]
    if not subject:
        return {}
    leases = L.unfinished_leases(state, cfg, now)
    lines = {
        line_key(state, state.items[i], cfg)
        for i, lz in leases.items()
        if i in state.items and is_mine(lz)
    }
    out: dict[str, str] = {}
    for item_id, lz in leases.items():
        other = state.items.get(item_id)
        if other is None or is_mine(lz):
            continue
        if lines and line_key(state, other, cfg) not in lines:
            continue  # another release line is another branch: no clash, as for `update`
        changed: list[str] | None = None
        loaded = False
        for p in subject:
            if p in out:
                continue
            names = _names(lz, p, shared)
            if not names:
                continue
            if any(not _wild(g) for g in names):
                hit = True
            else:
                if not loaded:  # one git read per lease, not per path
                    changed, loaded = L.holder_changed_paths(log, other), True
                hit = changed is None or p in changed
            if hit:
                out[p] = f"{item_id} ({lz.holder})"
    return out


def _names(lz: Lease, path: str, shared: list[str]) -> list[str]:
    """The globs of ``lz`` that claim ``path`` and are not shared. An exact one always holds
    it; a wildcard only once its holder has changed that path (or git cannot say), as
    `update` counts it."""
    return [g for g in lz.globs if not is_shared(g, shared) and path_in_glob(path, g)]
