"""Where the package and its shipped data live — asked once, answered once.

Four modules each worked this out for themselves from their own `__file__`:

    services/prompts.py     Path(__file__).resolve().parent / "templates" / "prompts"
    services/companions.py  Path(__file__).resolve().parent / "templates" / ...
    services/adopt.py       Path(__file__).resolve().parents[1]
    services/enforce.py     Path(__file__).resolve().parents[1]

Every one of them was correct while those files sat directly under `ddflow/`, and every
one of them broke the moment the package was split into layers — silently, because a
path that does not exist produces a "templates are missing, reinstall the package"
message that blames packaging, and a wrong `PYTHONPATH` produces a spawned server that
imports nothing, prints nothing, and leaves its client waiting on a handshake forever.
The full suite ran for two hours before that one was killed.

Counting directory levels above `__file__` encodes a file's *location* into its
*behaviour*. Anchoring on the package object instead does not: `ddflow.__file__` is
wherever the package is, installed or in a source tree, and a module that moves between
layers keeps working.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def package_dir() -> Path:
    """The `ddflow/` package directory itself."""
    import ddflow

    return Path(ddflow.__file__).resolve().parent


@lru_cache(maxsize=1)
def package_parent() -> Path:
    """The directory that must be on `PYTHONPATH` to `import ddflow`.

    Written into the MCP configs `adopt` generates and into the git hook `enforce`
    installs, so both spawn a Python that can actually find the package.
    """
    return package_dir().parent


#: Overrides where durable launch lines point. ddflow's own tests set it to the tree
#: under test, so hooks they install exercise THAT code, not the primary checkout's.
LAUNCH_ROOT_ENV = "DDFLOW_LAUNCH_ROOT"


def primary_checkout(tree: Path) -> Path | None:
    """The primary checkout of `tree` when `tree` is a LINKED git worktree holding a
    ddflow source tree whose primary holds one too; else None.

    Read from the files git keeps (`<tree>/.git` is a `gitdir:` pointer, the gitdir
    names its `commondir`) rather than by running git: this is asked while writing a
    hook, in whatever environment that happens.
    """
    if not (Path(tree) / ".git").is_file():
        return None
    common = common_dir(tree, ask_git=False)
    if common is None:
        return None
    # A bare or `--separate-git-dir` common dir has no checkout beside it: its parent
    # is just a directory, and one holding an unrelated `ddflow/` is not a primary.
    if common.name != ".git":
        return None
    primary = common.parent
    if primary == Path(tree).resolve() or not (primary / "ddflow" / "__init__.py").is_file():
        return None
    return primary


def common_dir(path: Path | str, *, ask_git: bool = True) -> Path | None:
    """The git common directory of the repository or linked worktree checked out at
    ``path`` (the shared `.git`), resolved; None when ``path`` is not one.

    The one answer. Three copies disagreed on odd layouts: the identity declaration read
    the files git keeps, the derived-id check ran `git rev-parse --git-common-dir`, and the
    primary-checkout probe read `commondir` its own way. This reads the files (a `.git`
    directory, or a `.git` file naming a gitdir whose `commondir` names the shared one; no
    `commondir` means a separate git dir or a submodule, which is its own), and only when
    that finds none -- ``path`` is a subdirectory, or git is told where to look through
    GIT_DIR -- asks git, unless ``ask_git`` is false (the per-call CLI path reads files
    only: it runs on every command in a worktree).
    """
    git = Path(path) / ".git"
    if git.is_dir():
        return git.resolve()
    try:
        line = git.read_bytes().decode("utf-8", "surrogateescape").strip()
    except OSError:
        line = ""
    if line.startswith("gitdir:"):
        gitdir = (Path(path) / line[len("gitdir:") :].strip()).resolve()
        try:
            raw = (gitdir / "commondir").read_bytes().decode("utf-8", "surrogateescape")
            return (gitdir / raw.strip()).resolve()
        except OSError:
            return gitdir
    if not ask_git:
        return None
    from . import git as G

    r = G.run(str(path), "rev-parse", "--git-common-dir", timeout=G.PROBE_TIMEOUT)
    if not r.ok or not r.out:
        return None
    found = Path(r.out)
    return (Path(path) / found).resolve() if not found.is_absolute() else found.resolve()


def launch_parent() -> Path:
    """What durable launch lines -- an MCP entry, a git hook, a SessionStart hook --
    put on `PYTHONPATH`.

    `package_parent()`, except in a LINKED worktree of ddflow: that tree is removed when
    its branch merges, and every line pointing into it then fails -- the MCP server
    cannot import and each git hook fails closed. Its primary checkout outlives it and
    is where the latest code lands, so a line written from a worktree points there
    (bug B-adopt-worktree-path). `DDFLOW_LAUNCH_ROOT` overrides both.
    """
    import os

    forced = os.environ.get(LAUNCH_ROOT_ENV)
    if forced:
        return Path(forced)
    here = package_parent()
    return primary_checkout(here) or here


def launch_python() -> str:
    """The interpreter for those lines: `sys.executable`, unless it lives inside the
    worktree `launch_parent()` redirected away from -- a per-worktree venv goes with the
    worktree -- in which case the primary checkout's own venv, when it has one, else an
    interpreter outside the worktree that is PROBED to import the primary's MCP server,
    else `sys.executable`, which `enforce.redirect_note` then warns about."""
    import os
    import sys

    here, target = package_parent(), launch_parent()
    # The venv DIRECTORY resolved, not the interpreter: a venv's python is a symlink to
    # the system one, while its directory is what lives (or not) inside the worktree --
    # possibly reached through a symlinked path (`/home/delian/src` is `/ai/delian/src`).
    written = Path(os.path.abspath(sys.executable))
    exe_dir = written.parent.resolve()
    if target == here:
        return sys.executable
    if not exe_dir.is_relative_to(here):
        # The venv itself lives outside the worktree -- but the path to it may still run
        # THROUGH the worktree (`/wt/.venv -> /shared/venv`), and that path dies with it.
        # Write the venv's real location instead, keeping the interpreter's own name.
        # Ancestors compared RESOLVED, one by one: the worktree may itself be reached by
        # an alias (`/home/delian/src` is `/ai/delian/src`).
        via_tree = any(a.resolve() == here for a in written.parents)
        return str(exe_dir / written.name) if via_tree else sys.executable
    for cand in (target / ".venv" / "bin" / "python3", target / ".venv" / "bin" / "python"):
        if cand.is_file():
            return str(cand)
    # No venv in the primary: any interpreter OUTSIDE the worktree that can import the
    # primary's MCP server -- deps and all -- outlives the worktree. Probed, because a
    # base python without the dependencies would fail as surely as a deleted one.
    import shutil

    outside = [getattr(sys, "_base_executable", ""), shutil.which("python3") or ""]
    for cand in dict.fromkeys(c for c in outside if c):
        cand_dir = Path(os.path.abspath(cand)).parent.resolve()
        if not cand_dir.is_relative_to(here) and _imports_ddflow(cand, str(target)):
            return cand
    return sys.executable


def _imports_ddflow(python: str, root: str) -> bool:
    """Whether `python` imports ddflow's MCP server from `root` (cheap, bounded)."""
    import os
    import subprocess

    from .proc import run

    env = {**os.environ, "PYTHONPATH": root}
    try:
        return (
            # Through `proc.run`: stdin detached, since in the MCP server stdin IS the
            # JSON-RPC stream and a child holding it would eat the next request.
            run(
                [python, "-c", "import ddflow.surfaces.mcp"],
                env=env,
                # `-c` puts the working directory first on sys.path: run it FROM the root,
                # or whatever ddflow sits in the caller's directory answers instead.
                cwd=root,
                capture_output=True,
                timeout=30,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def redirected_from() -> Path | None:
    """The linked worktree launch lines were redirected away from -- only when the
    redirect is the worktree one, never for an explicit `DDFLOW_LAUNCH_ROOT`."""
    import os

    if os.environ.get(LAUNCH_ROOT_ENV):
        return None
    here = package_parent()
    primary = primary_checkout(here)
    return here if primary is not None and primary == launch_parent() else None


def templates_dir() -> Path:
    """Shipped template data. Present in a wheel; `tests/test_packaging.py` proves it.

    The invariant that test exists for: templates living *beside* the package rather
    than *inside* it were absent from every wheel while being perfectly present in
    every source checkout — invisible to developers and fatal to users.
    """
    return package_dir() / "templates"
