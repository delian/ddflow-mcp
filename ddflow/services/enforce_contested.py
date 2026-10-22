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
    in_tree: Callable[[Lease], bool],
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
    mine_lines = _lines_of_mine(state, cfg, subject, is_mine, in_tree, now)
    out: dict[str, str] = {}
    for item_id, lz in L.unfinished_leases(state, cfg, now).items():
        other = state.items.get(item_id)
        if other is None or is_mine(lz):
            continue
        other_line = line_key(state, other, cfg)
        cache: dict[str, list[str] | None] = {}  # one git read per lease, not per path
        for p in subject:
            if p in out or other_line not in mine_lines[p]:
                continue  # taken already, or another release line: another branch, as for `update`
            if _holds_path(log, other, lz, p, shared, cache):
                out[p] = f"{item_id} ({lz.holder})"
    return out


def _lines_of_mine(
    state: State,
    cfg: Config,
    paths: list[str],
    is_mine: Callable[[Lease], bool],
    in_tree: Callable[[Lease], bool],
    now: float,
) -> dict[str, set[str]]:
    """path -> the release lines the commit can be for: those of the committer's leases that
    cover it. A lease made
    for the tree being committed in is the commit's own item, so when one covers the path
    only its line counts, not the lines of the committer's other items."""
    seen: dict[str, list[tuple[str, bool]]] = {p: [] for p in paths}
    for item_id, lz in state.active_leases(now, cfg.lease.grace_s).items():
        it = state.items.get(item_id)
        if it is None or not is_mine(lz):
            continue
        line, here = line_key(state, it, cfg), in_tree(lz)
        for p in paths:
            if any(path_in_glob(p, g) for g in lz.globs):
                seen[p].append((line, here))
    return {
        p: {ln for ln, here in hits if here or not any(h for _, h in hits)}
        for p, hits in seen.items()
    }


def _names(lz: Lease, path: str, shared: list[str]) -> list[str]:
    """The globs of ``lz`` that claim ``path`` and are not shared. An exact one always holds
    it; a wildcard only once its holder has changed that path (or git cannot say), as
    `update` counts it."""
    return [g for g in lz.globs if not is_shared(g, shared) and path_in_glob(path, g)]


def _holds_path(log: EventLog, other, lz: Lease, path: str, shared: list[str], cache: dict) -> bool:
    names = _names(lz, path, shared)
    if not names:
        return False
    if any(not _wild(g) for g in names):
        return True
    if "changed" not in cache:
        cache["changed"] = L.holder_changed_paths(log, other)
    return cache["changed"] is None or path in cache["changed"]
