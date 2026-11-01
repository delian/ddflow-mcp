"""Creating (or adopting) the worktree for an item."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from ...config import Config
from .. import git as G
from .. import proc as P
from .. import worktree as _W
from ..container import default_worktree_root
from ._local import copy_local_files
from ._locate import Worktree, _ignore_inside, default_branch, repo_root
from ._remote import rev
from ._state import sync


def isolate_venv(root: Path, tree: Path) -> bool:
    """Give a linked worktree its own `.venv`, so `pip install -e .` / `uv pip install -e .`
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

    Python/uv projects only (told by `uv.lock` beside the primary, the same signal
    `gates.testcmd` uses), and only when `uv` is on PATH. `--seed` ships pip so that a
    bare `pip install -e .`/`uv pip install -e .` run inside the worktree resolves this
    venv (pip's and uv's virtualenv DISCOVERY walks up to the shared venv only when the
    worktree has none of its own). A failure to make the venv is skipped, never raised —
    a claim must not die because a venv could not be made, exactly as `copy_local_files`
    never raises.
    """
    if not (Path(root) / "uv.lock").is_file():
        return False
    venv = Path(tree) / ".venv"
    if (venv / "pyvenv.cfg").is_file():
        return False  # a real venv is already there
    if venv.exists():
        # A torn leftover (a `.venv` with no `pyvenv.cfg`, from a create that died
        # mid-write) is not a virtualenv: clear it so `uv venv` builds a real one instead
        # of skipping the invalid directory and leaving discovery able to reach the
        # primary `.venv` (bug B4e7af0f14f).
        shutil.rmtree(venv, ignore_errors=True)
    uv = shutil.which("uv")
    if uv is None:
        return False
    made = P.capture(
        [uv, "venv", "--seed", "--quiet", "--python", sys.executable, str(venv)],
        timeout=P.TIMEOUTS["probe"],
    )
    if not made.ran or made.returncode != 0:
        # Seeding pip needs its wheel; when it cannot run (an offline cache, a write the
        # OS refused), still give the worktree its own `.venv` so virtualenv discovery
        # never reaches the shared one — the whole point of this guard (bug B4e7af0f14f).
        P.capture(
            [uv, "venv", "--quiet", "--python", sys.executable, str(venv)],
            timeout=P.TIMEOUTS["probe"],
        )
    return (venv / "pyvenv.cfg").exists()


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
        sync(wt, cfg)
    isolate_venv(root, path)
    wt.local_files = copy_local_files(root, path, cfg.worktree.local_files)
    return wt
