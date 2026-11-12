"""Refuse to run when this process imported ddflow from a foreign lane's worktree.

``fix-B-cli-ran-code-from-another-lanes-worktree`` first put this guard in
``ddflow.surfaces.cli``, where its ``from ..infra import worktree`` and
``from ..services import install_info`` reached infra/services directly and broke the
surfaces-through-api contract. It lives here, in the api layer, where reaching
infra/services is allowed; the surface calls ``guard_foreign_launch`` through this module.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..core.outcome import FAIL, OK
from ..infra import worktree as W
from ..services import install_info as I


def _deliberate_outside_launch(foreign: I.ForeignLaunch) -> bool:
    """A launcher put the tree on PYTHONPATH itself and the caller stands outside this
    checkout's family: the code was chosen on purpose and no lane of this checkout is running
    it by accident (a test's sandbox runs the tree under test from its own temp repository).

    Both halves are needed. A tree that came through the shared editable install is the
    redirect this guard exists for, and a caller inside the checkout (the primary, any lane)
    running another lane's tree by PYTHONPATH is still running another lane's code.
    """
    try:
        return I.named_on_pythonpath(foreign.worktree) and not I.in_checkout_family(
            Path(os.getcwd()), foreign.primary
        )
    except OSError:
        return False


def guard_foreign_launch() -> int:
    """The command's exit status: ``OK`` to run, ``FAIL`` after refusing a foreign import."""
    try:
        foreign = I.foreign_launch_root()
    except Exception:
        return OK
    if foreign is None:
        return OK
    try:
        own = W.current(Path(os.getcwd()))
    except Exception:
        # The import IS from a linked worktree (or the guard would have returned above),
        # so running it could execute another lane's unmerged code — the failure this
        # guard exists for — while not identifying the caller is NOT evidence the import
        # is the caller's own tree. Refuse, and repoint nothing: a heal on an unproven
        # foreignity would strand the caller's own tree if git merely misread it.
        print(
            f"ddflow: {I.FOREIGN_LAUNCH_REFUSAL}"
            f" {I.normalise_path(foreign.worktree)}, but the caller's own tree cannot be"
            " identified; refusing to run code that may be another lane's — re-run once"
            " the checkout is reachable",
            file=sys.stderr,
        )
        return FAIL
    if own is not None and own.path.resolve() == foreign.worktree.resolve():
        return OK  # the very tree this code came from: legitimate, not foreign
    if _deliberate_outside_launch(foreign):
        return OK
    try:
        I.repoint_shared_editable(foreign.worktree, foreign.primary)
    except Exception:
        pass
    print(
        f"ddflow: {I.FOREIGN_LAUNCH_REFUSAL}"
        f" {I.normalise_path(foreign.worktree)}, not the primary or your own tree;"
        " the shared editable install was reset to"
        f" {I.normalise_path(foreign.primary)} — re-run the command",
        file=sys.stderr,
    )
    return FAIL
