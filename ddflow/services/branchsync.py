"""`ddflow sync`: merge an item's recorded base into its branch, only when that is safe.

Merge, never rebase: leases and recorded gate results name commits. The measure is
`drift.measure` against the item's recorded base (D-item-base-branch), narrowed to the
item's globs (D-drift-measure). Pin files are settled by the merge driver during the merge;
any OTHER conflict aborts the merge, so the branch is left exactly as it was.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.model import ABANDONED, DONE
from ..infra import git as G
from ..infra import worktree as W
from . import drift as D
from .pins import pin_files


@dataclass
class SyncResult:
    base: str
    #: "current" | "behind" (--check) | "merged" | "refused" | "conflict" | "unknown"
    status: str
    behind: int | None = 0
    relevant: int = 0
    merged_pins: list[str] = field(default_factory=list)
    conflicts: dict[str, str] = field(default_factory=dict)  # path -> who last changed it on base
    dirty: list[str] = field(default_factory=list)
    detail: str = ""
    #: False only when `git merge --abort` itself failed after a conflict.
    aborted: bool = True
    #: `--keep-local`: the stash holding the uncommitted work when it could NOT be put back
    #: (apply conflicted); "" when nothing was stashed or it was restored and dropped.
    kept_stash: str = ""


def tree_item(st, repo: Path, tree: Path):
    """The live item whose worktree is ``tree``, or None."""
    tree = tree.resolve()
    found = [
        it
        for it in st.items.values()
        if it.worktree and not it.removed and W.load_path(repo, it.worktree).resolve() == tree
    ]
    found.sort(key=lambda it: it.state in (DONE, ABANDONED))  # a live item wins (drift.item_base)
    return found[0] if found else None


def _last_author(tree: Path, base: str, path: str) -> str:
    r = W.git(tree, "log", "-1", "--format=%an %h %s", base, "--", path)
    return r.out if r.ok and r.out else "unknown"


def _unsafe(out: SyncResult, tree: Path, keep_local: bool = False) -> SyncResult | None:
    """The refusal that stops a merge before it starts, or None when the tree is safe."""
    if W.merging(tree) is not False:
        return _stop(
            out, "refused", detail="a merge is already in progress here (or git could not say)"
        )
    st = G.status(tree, untracked="no")
    if st is None:
        return _stop(out, "refused", detail="git could not read the working tree status")
    if st and not keep_local:
        dirty = [e.path for e in st]
        return _stop(
            out,
            "refused",
            dirty=dirty,
            detail=(
                "uncommitted changes; commit them first, or `ddflow sync --keep-local` "
                "merges anyway and puts them back"
            ),
        )
    return None


def _stash_find(tree: Path, tag: str) -> tuple[str, str]:
    """(sha, ``stash@{n}``) of the stash entry named ``tag``, or ("", "")."""
    r = W.git(tree, "stash", "list", "--format=%H %gd %gs")
    for line in r.out.splitlines() if r.ok else ():
        sha, ref, subject = [*line.split(" ", 2), "", ""][:3]
        if tag in subject:
            return sha, ref
    return "", ""


def _stash_away(tree: Path, tag: str) -> str:
    """Stash tracked, staged and untracked work under ``tag``; "" when there was none,
    else the entry's sha. Never a bare stash: the stack is shared by every worktree, so the
    entry is found again BY TAG (another session may push meanwhile)."""
    r = W.git(tree, "stash", "push", "-u", "-m", tag)
    if not r.ok:
        raise RuntimeError(r.err or r.out)
    return _stash_find(tree, tag)[0]


def _stash_back(tree: Path, tag: str, sha: str) -> str:
    """Re-apply the stash ``sha`` and drop the entry named ``tag``. "" when restored, else
    why not -- the entry then stays, holding the work."""
    r = W.git(tree, "stash", "apply", sha)
    if not r.ok:
        return r.err or r.out or "git stash apply failed"
    _sha, ref = _stash_find(tree, tag)
    if ref:
        W.git(tree, "stash", "drop", ref)
    return ""


def _failed_merge(out: SyncResult, tree: Path, base: str, r) -> SyncResult:
    """Classify a merge git refused: a pre-merge refusal, or a real conflict (aborted)."""
    files = G.unmerged(tree)
    if files is None and W.merging(tree) is False:
        return _stop(out, "refused", detail=f"git could not list the conflicts: {r.err or r.out}")
    files = files or []  # unlistable but mid-merge: abort below, names unknown
    if not files and W.merging(tree) is False:
        # Git refused before it began (an untracked file the base adds, say): not a
        # conflict, and there is nothing to abort.
        return _stop(out, "refused", detail=r.err or r.out)
    aborted = W.git(tree, "merge", "--abort")
    out.conflicts = {f: _last_author(tree, base, f) for f in files}
    out.aborted = aborted.ok
    detail = r.err or r.out
    if not aborted.ok:
        detail += (
            f"\n\nAborting the merge ALSO failed ({aborted.err or aborted.out}): {tree} may be "
            f"left mid-merge; run `git -C {tree} merge --abort` by hand."
        )
    return _stop(out, "conflict", detail=detail)


def sync(
    repo: Path,
    cfg,
    tree: Path,
    base: str,
    globs: list[str],
    *,
    check: bool,
    keep_local: bool = False,
) -> SyncResult:
    rep = D.measure(repo, tree, base, cfg=cfg, globs=globs)
    if rep.behind is None:
        return SyncResult(rep.base, "unknown", None, detail=rep.detail)
    if rep.behind == 0:
        return SyncResult(rep.base, "current", 0)
    out = SyncResult(rep.base, "behind", rep.behind, len(rep.relevant))
    if check:
        return out
    if (refusal := _unsafe(out, tree, keep_local)) is not None:
        return refusal
    tag = ""
    sha = ""
    if keep_local:
        tag = f"ddflow-sync-keep-local-{os.getpid()}-{time.time_ns()}"
        try:
            sha = _stash_away(tree, tag)
        except RuntimeError as e:
            return _stop(out, "refused", detail=f"could not set the uncommitted work aside: {e}")
    r = W.git(tree, "merge", "--no-edit", rep.base)
    if not r.ok:
        out = _failed_merge(out, tree, rep.base, r)
        return _restore(out, tree, tag, sha)
    out.status = "merged"
    out = _restore(out, tree, tag, sha)
    moved = {p for c in rep.commits for p in c.paths}
    out.merged_pins = sorted(
        f for f in set(pin_files(repo, cfg)) if f in moved or _inside_dir(f, moved)
    )
    return out


def _restore(out: SyncResult, tree: Path, tag: str, sha: str) -> SyncResult:
    if not sha:
        return out
    if W.merging(tree) is not False:  # an abort that failed: the stash stays, untouched
        out.kept_stash = sha
        out.detail += f"\nyour uncommitted work is kept in stash {sha[:12]} ({tag})"
        return out
    why = _stash_back(tree, tag, sha)
    if why:
        out.kept_stash = sha
        out.detail += (
            f"\nyour uncommitted work could not be put back ({why.strip()}); it is kept in "
            f"stash {sha[:12]} ({tag}): `git stash apply {sha}`"
        )
    return out


def _stop(out: SyncResult, status: str, **kw) -> SyncResult:
    out.status = status
    for k, v in kw.items():
        setattr(out, k, v)
    return out


def _inside_dir(pin: str, paths: set[str]) -> bool:
    """A pin that names a directory (`tests/golden/__snapshots__/`) is touched by any path under it."""
    return pin.endswith("/") and any(p.startswith(pin) for p in paths)
