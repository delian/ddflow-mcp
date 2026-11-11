"""Creating (or adopting) the worktree for an item."""

from __future__ import annotations

import contextlib
import shutil
import sys
from pathlib import Path

from ...config import Config
from .. import fsio
from .. import git as G
from .. import proc as P
from .. import worktree as _W
from ..container import default_worktree_root
from ._local import copy_local_files
from ._locate import Worktree, _ignore_inside, default_branch, repo_root
from ._merge import merging
from ._remote import rev
from ._state import sync


def _remove(path: Path) -> None:
    """Best-effort removal of a symlink, file or directory; never raises."""
    if path.is_symlink() or path.is_file():
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
    elif path.is_dir():
        fsio.rmtree_owned(path)


def isolate_venv(root: Path, tree: Path) -> bool:
    """Give a linked worktree its own `.venv`, so `uv pip install -e .` / `uv run`
    run inside it resolve THAT environment and never repoint the primary checkout's shared
    one (bug B4e7af0f14f).

    ddflow's shared `.venv` (at the primary checkout root) is where its console scripts —
    `ddflow`, `ddflow-mcp` — live and where the running agents' ddflow resolves. A worktree
    sits UNDER the primary (`[worktree].root` is `.ddflow/worktrees`), so a worktree with no
    `.venv` of its own that runs an editable install walks up the directory tree, discovers
    the shared venv, and rewrites its editable install's `.pth`/`direct_url.json` to point
    at the worktree. When the worktree is removed the dangling `.pth` makes every
    console-script call fail with ModuleNotFoundError. A worktree-local `.venv` is
    git-ignored and dies with the tree, so it can never dangle against a removed path.

    Python/uv projects only (told by `uv.lock` beside the worktree OR the primary), and
    only when `uv` is on PATH. `uv pip`'s and `uv run`'s
    virtualenv DISCOVERY walks up to the shared venv only when the worktree has none of
    its own; those tools need no native `pip` in the venv, so a bare `.venv` is enough.
    A failure to make the venv is skipped, never raised — a claim must not die because a
    venv could not be made, exactly as `copy_local_files` never raises.
    """
    if not ((Path(root) / "uv.lock").is_file() or (Path(tree) / "uv.lock").is_file()):
        return False
    venv = Path(tree) / ".venv"
    if (venv / "pyvenv.cfg").is_file() and not venv.is_symlink():
        return False  # a real, LOCAL venv already covers discovery
    uv = shutil.which("uv")
    if uv is None:
        return False
    # Build the replacement in a sibling temp path and swap it in only after it succeeds.
    # A `.venv` that cannot be replaced (a symlink that stays, or `uv venv` that fails) is
    # left alone, never destroyed while the shared env stays reachable via discovery.
    tmp = venv.with_name(venv.name + ".tmp")
    _remove(tmp)
    made = P.capture(
        [uv, "venv", "--quiet", "--python", sys.executable, str(tmp)], timeout=P.TIMEOUTS["probe"]
    )
    if not made.ran or made.returncode != 0 or not (tmp / "pyvenv.cfg").is_file():
        _remove(tmp)
        return False
    _remove(venv)  # the symlink / torn leftover, now that the replacement is built
    if venv.is_symlink() or venv.exists():
        _remove(tmp)  # the old `.venv` could not be cleared; don't leave a stranded tmp
        return False
    with contextlib.suppress(OSError):
        tmp.rename(venv)
    if not (venv / "pyvenv.cfg").is_file():
        _remove(tmp)  # the rename failed; don't leave a stranded `.venv.tmp`
        return False
    return True


def _abandon_broken_sync(wt: Worktree, r: G.GitResult) -> str:
    """Abort the merge `sync` left half-done and describe it.

    A freshly-created tree is clean, so a failed `sync` here is a real merge failure:
    a branch that has diverged from base leaves MERGE_HEAD set, staged paths and
    conflict markers the agent never chose. Abort that unfinished merge so the tree is
    back at the branch tip, and say so — the wrong answer is `created` with no warning,
    which let an agent commit a merge it did not make (B-claim-sync-conflict-silent).
    """
    detail = (r.out or r.err).strip().splitlines() or ["base sync failed"]
    if merging(wt.path) is not True:
        # Nothing to abort: git refused before it judged the branch (or the tree is
        # unreadable). The note still says the sync did not land cleanly.
        return f"base sync did not land: {detail[0].strip()}"
    aborted = _W.git(wt.path, "merge", "--abort")
    note = f"base sync conflicted and was aborted: {detail[0].strip()}"
    if not aborted.ok:
        note += f" (aborting the merge ALSO failed: {aborted.err or aborted.out})"
    return note


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
        isolate_venv(root, path)
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
        r = sync(wt, cfg)
        if not r.ok:
            wt.sync_note = _abandon_broken_sync(wt, r)
    isolate_venv(root, path)
    wt.local_files = copy_local_files(root, path, cfg.worktree.local_files)
    return wt
