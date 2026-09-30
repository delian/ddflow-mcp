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
    marker = Path(tree) / ".git"
    if not marker.is_file():
        return None
    try:
        text = marker.read_text("utf-8").strip()
        if not text.startswith("gitdir:"):
            return None
        gitdir = (Path(tree) / text.split(":", 1)[1].strip()).resolve()
        common = (gitdir / (gitdir / "commondir").read_text("utf-8").strip()).resolve()
    except OSError:
        return None
    primary = common.parent
    if primary == Path(tree).resolve() or not (primary / "ddflow" / "__init__.py").is_file():
        return None
    return primary


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
    worktree -- in which case the primary checkout's own venv, when it has one."""
    import sys

    here, target = package_parent(), launch_parent()
    exe = Path(sys.executable)
    if target == here or not exe.is_relative_to(here):
        return sys.executable
    for cand in (target / ".venv" / "bin" / "python3", target / ".venv" / "bin" / "python"):
        if cand.is_file():
            return str(cand)
    return sys.executable


def redirected_from() -> Path | None:
    """The linked worktree launch lines were redirected away from, for the notice."""
    here = package_parent()
    return here if launch_parent() != here else None


def templates_dir() -> Path:
    """Shipped template data. Present in a wheel; `tests/test_packaging.py` proves it.

    The invariant that test exists for: templates living *beside* the package rather
    than *inside* it were absent from every wheel while being perfectly present in
    every source checkout — invisible to developers and fatal to users.
    """
    return package_dir() / "templates"
