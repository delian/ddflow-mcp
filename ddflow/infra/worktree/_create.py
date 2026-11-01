"""Creating (or adopting) the worktree for an item."""

from __future__ import annotations

from pathlib import Path

from ...config import Config
from .. import git as G
from .. import worktree as _W
from ..container import default_worktree_root
from ._local import copy_local_files
from ._locate import Worktree, _ignore_inside, default_branch, repo_root
from ._remote import rev
from ._state import sync


def create(repo: Path, cfg: Config, item_id: str, *, base: str = "", branch: str = "") -> Worktree:
    """Create (or adopt) the worktree for an item. Idempotent by design.

    Adoption matters for recovery: after a crash the agent restarts, finds the
    worktree already present, and continues in it rather than erroring or — far worse —
    creating a second tree and abandoning the first with its work inside.

    ``base`` and ``branch`` come from `core.flow` when a branching model names them (a
    gitflow ``feature/`` branch off ``develop``, a task stacked on a dependency's
    branch); empty keeps the trunk defaults.
    """
    root = repo_root(repo)
    base = base or cfg.worktree.base_ref or default_branch(root)
    name = _W.safe_name(item_id)
    branch = branch or f"{cfg.worktree.branch_prefix}{name}"

    # Inside a container the default sibling root lands on the ephemeral layer and is
    # destroyed on exit, taking uncommitted work with it. See container.py.
    wt_root = (root / default_worktree_root(cfg.worktree.root)).resolve()
    path = wt_root / name
    wt = Worktree(item=item_id, path=path, branch=branch, base=base)

    if path.exists() and (path / ".git").exists():
        _ignore_inside(root, wt_root)
        wt.local_files = copy_local_files(root, path, cfg.worktree.local_files)
        return wt  # adopt: the original fork sha is not this call's `rev(base)`
    wt_root.mkdir(parents=True, exist_ok=True)
    _ignore_inside(root, wt_root)

    have_branch = _W.git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").ok
    args = ["worktree", "add"]
    if have_branch:
        args += [str(path), branch]
    else:
        args += ["-b", branch, str(path), base]
    r = _W.git(root, *args)
    if not r.ok:
        raise G.GitError(f"worktree add failed for {item_id}: {r.err or r.out}")
    if not have_branch:
        # A NEW branch was created at base's tip: read the fork from the branch itself,
        # AFTER the add ran, so a concurrent base rewrite between resolving base and the
        # add cannot leave a stale sha that predates the branch (B05dce361fd critic #2).
        wt.base_sha = rev(root, branch)
    wt.created = True
    if cfg.worktree.sync_before_start and have_branch:
        sync(wt, cfg)
    wt.local_files = copy_local_files(root, path, cfg.worktree.local_files)
    return wt
