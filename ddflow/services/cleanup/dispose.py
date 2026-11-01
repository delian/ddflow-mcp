"""Tree and branch removal — the one guarded, logged path that deletes a worktree.

``dispose_tree`` is where merge, the PR flow, cleanup and onboarding all end
(B-uni-tree-lifecycle), and ``sweep_finished`` removes a finished item's leftover tree at
session start. Split out of ``ddflow/services/cleanup.py`` (rule r-small-files). It builds
on ``model`` and ``stranded`` and knows nothing about the survey/apply planning above it.
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path

from ...config import Config
from ...core import clock as CK
from ...core.model import DONE, Item, State, fold
from ...infra import worktree as W
from ...infra.log import EventLog
from ..leaseclock import localize
from .model import Disposal, _key
from .stranded import _protected


def record_item_removed(log: EventLog, it) -> None:
    """THE writer of `worktree.removed` (B54909478c4): the item's tree as `claim` recorded
    it (`it.worktree`, often repo-relative), whoever removed it -- merge, the PR flow or
    cleanup -- so a change to the event is made in one place."""
    log.append("worktree.removed", it.id, {"path": it.worktree})


def record_removed(log: EventLog, root: Path, path: str) -> None:
    """`worktree.removed` for every item whose recorded tree is ``path``, as `merge`
    writes it, so the fold stops pointing at a directory that is gone (B5e83fb22cb).
    Call it under ``log.transaction()``, right after the removal."""
    gone = _key(path)
    for it in fold(log.read_all(), strict=False).items.values():
        if it.worktree and _key(str(W.load_path(root, it.worktree))) == gone:
            record_item_removed(log, it)


def dispose_tree(
    repo: Path,
    cfg: Config,
    log: EventLog | None,
    wt: W.Worktree,
    *,
    item: Item | None = None,
    forced_by: str = "",
    landed_tip: str = "",
) -> Disposal:
    """THE removal of a worktree (B-uni-tree-lifecycle): merge, the PR flow, cleanup and
    onboarding all end here, so every one is guarded, serialised with the claims and logged.

    * **Under the log's lock, with the leases re-read**: a tree a LIVE lease holds, or an
      adopted one, is refused (``item``'s own lease -- the caller holds it -- does not count).
      A claim appended between a survey and this call is seen, or waits and then finds no tree.
    * **Guarded by git**: without ``forced_by``, `W.remove` refuses a tree with uncommitted
      files, unmerged commits, or one it could not measure. ``forced_by`` is the caller's
      PROOF that removing whatever the tree holds is safe (the PR head merged on the forge;
      an inspection that found nothing beyond caches): it makes the removal forced, and the
      caller measured the tree itself.
    * ``landed_tip`` is the weaker proof (a landed item's merge/pin leftovers are the only
      commits up to that commit): not forced, so git refuses a file written after the caller's check itself.
    * **Logged**: a removal that worked appends `worktree.removed` -- for ``item`` when given,
      else for every item whose recorded tree is this path (B5e83fb22cb).

    ``log`` None is a project that keeps no event log: no lock, no guard, nothing to record.
    """
    root = W.repo_root(repo)
    with log.transaction() if log else contextlib.nullcontext():
        if log is not None:
            guard = _protected(
                root,
                cfg,
                localize(fold(log.read_all(), strict=False), log, time.time()),
                ignore=item.id if item else "",
            )
            kind, reason = guard.why(str(wt.path), wt.branch)
            if kind:
                return Disposal("refused", reason, kind)
        had_branch = bool(wt.branch) and W.branch_exists(root, wt.branch)
        r = W.remove(repo, cfg, wt, force=bool(forced_by), landed_tip=landed_tip)
        if not r.ok:
            return Disposal("failed", (r.err or r.out).strip() or f"git exit {r.code}")
        if log is not None:
            if item is not None:
                record_item_removed(log, item)
            else:
                record_removed(log, root, str(wt.path))
    if had_branch and W.branch_exists(root, wt.branch):
        rb = W.git(root, "branch", "-d", wt.branch)
        if not rb.ok:
            left = (rb.err or rb.out).strip() or f"git exit {rb.code}"
            return Disposal("removed", branch_left=left, had_branch=True)
    return Disposal("removed", had_branch=had_branch)


def stands_in(tree: Path, *wheres: Path | None) -> bool:
    """Is any of ``wheres`` the tree or inside it? Deleting a shell's working directory makes
    its next `pwd` fail (B1172da8c35), so such a tree is never removed under its caller."""
    try:
        root = tree.resolve()
    except OSError:
        return False
    for where in wheres:
        if where is None:
            continue
        try:
            here = Path(where).resolve()
        except OSError:
            continue
        if here == root or root in here.parents:
            return True
    return False


def dispose_finished(
    repo: Path, cfg: Config, log: EventLog, it: Item, *, callers: tuple[Path | None, ...] = ()
) -> tuple[bool, str]:
    """(removed, why kept) for the OWN tree and branch of an item that is finished with:
    done, abandoned or removed (B79319c6ada, B4757c428f4).

    The one rule `merge` already follows, for the other ways an item ends. Only what
    `dispose_tree` allows goes: a clean tree with nothing unmerged, never an adopted one,
    never one a live lease holds, and never the directory a caller stands in (``callers``,
    as for `merge`). A tree holding uncommitted files or unmerged commits is KEPT and the
    reason says how to look at it; "" means there was nothing to do.
    """
    if not (cfg.worktree.dispose_finished and cfg.worktree.remove_on_merge):
        return False, ""  # `remove_on_merge = false` is the standing "keep my trees" setting
    if not it.worktree or it.adopted:
        return False, ""
    path = W.load_path(repo, it.worktree)
    if not path.exists():
        return _drop_branch(repo, cfg, log, it)
    if stands_in(path, *callers):
        return False, (
            f"worktree {path} kept: you are standing in it, and removing it would leave your "
            f"shell in a deleted directory. cd {W.repo_root(repo)} -- the next `ddflow brief` "
            f"or `ddflow cleanup --apply` removes it."
        )
    base = cfg.worktree.base_ref or W.default_branch(repo)
    wt = W.Worktree(item=it.id, path=path, branch=it.branch, base=base)
    # late-bound: honours monkeypatch of the package attr
    from . import dispose_tree as _dispose_tree

    gone = _dispose_tree(repo, cfg, log, wt, item=it)
    if gone.removed:
        return True, (f"branch {it.branch} left: {gone.branch_left}" if gone.branch_left else "")
    return False, f"worktree {path} and branch {it.branch} kept: {gone.why}"


def _drop_branch(repo: Path, cfg: Config, log: EventLog, it: Item) -> tuple[bool, str]:
    """The branch of an item whose tree is already gone (removed by hand): deleted when it is
    fully merged (`git branch -d` never deletes unmerged work), never while a live lease holds
    it, and named in the answer when it is kept."""
    root = W.repo_root(repo)
    if not it.branch or not W.branch_exists(root, it.branch):
        return False, ""
    with log.transaction():
        held = _protected(root, cfg, localize(fold(log.read_all(), strict=False), log, time.time()))
        kind, _why = held.why("", it.branch)
        if kind:
            return False, ""
        done = W.git(root, "branch", "-d", it.branch)
    if done.ok:
        return False, f"branch {it.branch} removed (its worktree was already gone)"
    return False, f"branch {it.branch} kept: {(done.err or done.out).strip() or 'not merged'}"


def _finish_stamps(log: EventLog) -> dict[str, str]:
    """When each abandoned or removed item was finished with: its last such event. Read only
    when a sweep meets such an item, since the fold keeps no time for them."""
    stamps: dict[str, str] = {}
    for ev in log.read_all():
        if ev.kind in ("item.abandoned", "task.removed", "phase.removed"):
            stamps[ev.subject] = ev.ts
    return stamps


def sweep_finished(
    repo: Path,
    cfg: Config,
    log: EventLog,
    state: State,
    *,
    now: float | None = None,
    callers: tuple[Path | None, ...] | None = None,
) -> list[str]:
    """What a session start removes of a finished item's leftover tree (B4757c428f4): `merge`
    keeps the tree it was run from, `abandon` and `remove` keep one the caller stood in or one
    holding work, and nothing else removed them later.

    Covers done, abandoned and removed items, each only once it has been finished longer
    than ``worktree.sweep_min_age_s`` (an item with no known finish time is never old): the
    tree an agent finished in a moment ago may be the directory it still stands in, and the
    caller guard protects only THIS caller. Never the directory this process or the given
    ``callers`` stand in; a tree holding work is kept and named. Returns the sentences for
    what was removed or kept."""
    if not cfg.worktree.dispose_finished:
        return []
    now = time.time() if now is None else now
    if callers is None:  # a direct caller: the process stands where its shell does
        callers = ()
    try:
        callers = (*callers, Path.cwd())  # also this process's own directory
    except OSError:
        pass
    out: list[str] = []
    stamps: dict[str, str] | None = None
    for it in state.items.values():
        finished = it.removed or it.terminal
        if not finished or not it.worktree or it.adopted or it.lease:
            continue
        if it.state == DONE and not it.removed:
            stamp = it.completed_at
        else:
            stamps = _finish_stamps(log) if stamps is None else stamps
            stamp = stamps.get(it.id, "")
        finished_for = CK.age_s(stamp, now) if stamp else 0.0
        if finished_for < cfg.worktree.sweep_min_age_s:
            continue  # too fresh, or no finish time: the tree may be an agent's cwd
        removed, why = dispose_finished(repo, cfg, log, it, callers=callers)
        if removed:
            out.append(f"removed the finished worktree of {it.id}" + (f"; {why}" if why else ""))
        elif why:
            out.append(f"{it.id}: {why}")
    return out
