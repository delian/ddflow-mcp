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
    is_mine: Callable[[Lease], bool],
    in_tree: Callable[[Lease], bool],
    now: float,
) -> dict[str, str]:
    """path -> "item (holder)" for each of ``paths`` the committer covers by wildcard only
    and another live, unfinished lease on the same line covers too, as `update` judges it."""
    shared = shared_globs(cfg)
    unfinished = L.unfinished_leases(state, cfg, now)
    lines = _commit_lines(state, cfg, paths, unfinished, is_mine, in_tree, shared)
    out: dict[str, str] = {}
    for item_id, lz in unfinished.items():
        other = state.items.get(item_id)
        if other is None or is_mine(lz):
            continue
        other_line = line_key(state, other, cfg)
        cache: dict[str, list[str] | None] = {}  # one git read per lease, not per path
        for p, mine_lines in lines.items():
            if p in out or other_line not in mine_lines:
                continue  # taken already, or another release line: another branch, as for `update`
            if _holds_path(log, other, lz, p, shared, cache):
                out[p] = f"{item_id} ({lz.holder})"
    return out


def _commit_lines(
    state: State,
    cfg: Config,
    paths: list[str],
    unfinished: dict[str, Lease],
    is_mine: Callable[[Lease], bool],
    in_tree: Callable[[Lease], bool],
    shared: list[str],
) -> dict[str, set[str]]:
    """path -> the release line(s) its commit can be for, for each path the committer covers by
    WILDCARDS only. The leases covering a path are the committer's; the one made for the tree
    being committed in is the commit's own item, and when there is one only it counts, so
    the lines of the committer's other items never decide. A path the committer covers on two lines with no
    tree to tell them apart is for either, so a rival on any of them contests it; one an exact
    glob covers is left out: an exact holder is never overlapped."""
    out: dict[str, set[str]] = {}
    for p in paths:
        if is_shared(p, shared):
            continue
        cover = [
            (
                line_key(state, state.items[i], cfg),
                in_tree(lz),
                [g for g in lz.globs if path_in_glob(p, g)],
            )
            for i, lz in unfinished.items()
            if i in state.items and is_mine(lz)
        ]
        cover = [c for c in cover if c[2]]
        if any(here for _, here, _ in cover):
            cover = [c for c in cover if c[1]]
        lines = {line for line, _, _ in cover}
        if lines and all(_wild(g) for _, _, gs in cover for g in gs):
            out[p] = lines
    return out


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
