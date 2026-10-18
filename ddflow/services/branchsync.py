"""`ddflow sync`: merge an item's recorded base into its branch, only when that is safe.

Merge, never rebase: leases and recorded gate results name commits. The measure is
`drift.measure` against the item's recorded base (D-item-base-branch), narrowed to the
item's globs (D-drift-measure). Pin files are settled by the merge driver during the merge;
any OTHER conflict aborts the merge, so the branch is left exactly as it was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..infra import git as G
from ..infra import worktree as W
from . import drift as D


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


def tree_item(st, repo: Path, tree: Path):
    """The live item whose worktree is ``tree``, or None."""
    tree = tree.resolve()
    found = [
        it
        for it in st.items.values()
        if it.worktree and not it.removed and W.load_path(repo, it.worktree).resolve() == tree
    ]
    return found[0] if found else None


def _last_author(tree: Path, base: str, path: str) -> str:
    r = W.git(tree, "log", "-1", "--format=%an %h %s", base, "--", path)
    return r.out if r.ok and r.out else "unknown"


def sync(repo: Path, cfg, tree: Path, base: str, globs: list[str], *, check: bool) -> SyncResult:
    rep = D.measure(repo, tree, base, cfg=cfg, globs=globs)
    if rep.behind is None:
        return SyncResult(rep.base, "unknown", None, detail=rep.detail)
    if rep.behind == 0:
        return SyncResult(rep.base, "current", 0)
    out = SyncResult(rep.base, "behind", rep.behind, len(rep.relevant))
    if check:
        return out
    from .pins import pin_files  # deferred: pins imports a long chain

    pins = set(pin_files(repo, cfg))
    if W.merging(tree) is not False:
        return _stop(
            out, "refused", detail="a merge is already in progress here (or git could not say)"
        )
    st = G.status(tree, untracked="no")
    dirty = [e.path for e in st] if st is not None else None
    if dirty is None:
        return _stop(out, "refused", detail="git could not read the working tree status")
    if dirty:
        return _stop(out, "refused", dirty=dirty, detail="uncommitted changes; commit them first")
    r = W.git(tree, "merge", "--no-edit", rep.base)
    if not r.ok:
        files = G.unmerged(tree) or []
        if not files and W.merging(tree) is False:
            # Git refused before it began (an untracked file the base adds, say): not a
            # conflict, and there is nothing to abort.
            return _stop(out, "refused", detail=r.err or r.out)
        W.git(tree, "merge", "--abort")
        out.conflicts = {f: _last_author(tree, rep.base, f) for f in files}
        return _stop(out, "conflict", detail=r.err or r.out)
    out.status = "merged"
    out.merged_pins = sorted({p for c in rep.commits for p in c.paths} & pins)
    return out


def _stop(out: SyncResult, status: str, **kw) -> SyncResult:
    out.status = status
    for k, v in kw.items():
        setattr(out, k, v)
    return out
