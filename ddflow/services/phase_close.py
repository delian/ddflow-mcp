"""Close a phase whose work predates ddflow, without a phase gate pipeline.

The importer already does this for work it is importing: a phase whose tasks are all done
or closed in the SOURCE is written as `item.completed {"kind": "phase", "imported": true,
"evidence": ...}`, bypassing `services.completion.verdict()` and the whole phase pipeline --
steps (research, unit_tests, bug_hunt, dedupe, live_test, corrections, docs) that can only
be run for work this project actually did.

What was missing is the same door for a phase already IN the queue: the ones a plain
`ddflow import` never closed, or whose tasks were finished here afterwards. `ddflow brief`
nags "finished but still open" about them every session, and the only way out was
`ddflow complete --force`, which records nothing about WHY the gates did not run.

This module is that door and nothing else. It refuses unless the phase was created by
`ddflow import` (`Item.source`) and every task under it is done or abandoned, and it writes
the SAME event the importer writes -- so the two paths cannot describe one phase differently.
"""

from __future__ import annotations

from typing import Any

from ..core import outcome as O
from ..core.model import State, fold

# The importer's own predicate, not a second copy of it: "a phase the IMPORTER wrote -- the
# only kind it may ever complete" is exactly the set this command may close.
from .importer import _is_imported_phase

# The shared resolve-and-settle guard, not a second copy of `phase complete`'s (r-reuse-core).
from .phase_guard import resolve_phase, settled_tasks

KIND = "phase.close"


def close_imported(log: Any, st: State, phase_id: str, *, agent: str = "") -> O.Outcome:
    """Complete an imported phase whose every task is done or abandoned.

    Refused (exit 3) for anything else: a phase with open work is not finished, a phase the
    importer did not write is not this command's business, and an item that is not a phase
    is `ddflow complete`'s.
    """
    err, it = resolve_phase(st, KIND, phase_id)
    if err is not None:
        return err
    if not _is_imported_phase(it):
        return O.refused(
            KIND,
            f"{phase_id} was not created by `ddflow import` (it has no imported source): "
            f"a phase this project worked runs its pipeline -- `ddflow gate status {phase_id}`",
            id=phase_id,
        )
    err, tasks, done = settled_tasks(
        it, st, KIND, phase_id, hint="close them first, or file the work that remains"
    )
    if err is not None:
        return err
    # What is KNOWN, not what is assumed: the phase came from the importer, and every task
    # under it is settled. "completed before ddflow" would claim something this cannot
    # check -- a task may have been finished HERE afterwards (roborev on ab4179fb).
    evidence = (
        f"closed as imported work (imported from {it.source}); every one of the "
        f"{len(tasks)} task(s) under it is done or abandoned "
        f"({done} done, {len(tasks) - done} abandoned)"
    )
    # The importer's exact event: `imported` marks the authority, `evidence` is what a
    # reader (and `ddflow verify`) is told instead of a gate pipeline that never ran.
    log.append(
        "item.completed",
        phase_id,
        {"kind": "phase", "imported": True, "evidence": evidence},
    )
    return O.ok(KIND, id=phase_id, evidence=evidence, tasks=len(tasks), done=done)


def close(log: Any, phase_id: str, *, imported: bool, agent: str = "") -> O.Outcome:
    """The transaction around `close_imported`: refuse unless `imported`, then fold and write.

    Folding INSIDE the transaction, like `phase_add`: a task another agent closed since the
    caller loaded is then seen, so the check and the write cannot disagree.
    """
    if not imported:
        return O.refused(
            KIND,
            "this closes a phase whose work predates ddflow; pass `--imported` (the flag is "
            "the authority being used, and it is recorded as evidence)",
            id=phase_id,
        )
    with log.transaction():
        now = fold(log.read_all(), strict=False)
        return close_imported(log, now, phase_id, agent=agent)
