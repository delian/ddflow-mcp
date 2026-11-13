"""Kill-unwind: what a controller decides for a LIVE lane it is about to kill, in one switch.

The ACTIVE counterpart of `recover_auto` (which decides for STOPPED lanes): a kill
decides for a lane that is STILL RUNNING. Every kill stops the subagent and releases its
lease; the switch says what happens to the worktree:

* SOFT unwind (default): stop the subagent, release the lease, and PRESERVE the worktree --
  its commits and uncommitted edits survive and a later claim resumes them;
* HARD kill: stop the subagent, release the lease, and DISCARD the worktree -- the work is
  thrown away on purpose.

Guardrails first, mirroring `recover_auto`: an ADOPTED harness tree is the harness's own
working tree, still in use, so a kill never discards it (a hard kill over an adopted tree
falls back to soft, stopping and releasing but leaving the tree); a tree ddflow could not
measure is never guessed discardable and is preserved the same way.

Pure: no git, no clock, no I/O. ``salvageable`` is measured upstream the same way
``leases.recovery`` measures it, so the decision and any apply path read one answer,
testable without a repository.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .recover_auto import adopted_flag

#: Kill-unwind effects. Soft first: it loses nothing.
SOFT = "soft_unwind"  # stop the subagent + release the lease; worktree preserved
HARD = "hard_kill"  # stop the subagent + release the lease; worktree discarded

EFFECTS = (SOFT, HARD)


@dataclass(frozen=True)
class Kill:
    """One live lane's kill, as a pure decision."""

    item: str
    lane: str
    effect: str
    rationale: str
    hard: bool  #: the switch: True = discard the worktree, False = preserve it
    preserve: bool  #: keep the worktree (always True for a soft unwind)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide(
    item: str,
    lane: str,
    *,
    hard: bool = False,
    adopted: bool = False,
    salvageable: bool | None = None,
) -> Kill:
    """The one switch for a live lane a controller is about to kill.

    ``hard`` is the switch: False unwinds soft (preserve the worktree), True kills hard
    (discard it). Guardrails first -- a hard kill never discards an adopted tree or a
    tree it could not measure, it falls back to a soft unwind. Only when the switch says
    hard AND the tree is measured (``salvageable`` True or False) is the worktree
    discarded: ``salvageable`` then names what the discard throws away (True = its
    uncommitted work is lost; False = clean, nothing is lost), so it never blocks the
    discard the controller asked for. A soft unwind always preserves.
    """
    if not hard:
        return Kill(
            item,
            lane,
            SOFT,
            "soft unwind: stop the subagent, release the lease, and preserve the worktree; "
            "its work survives and a later claim resumes it",
            False,
            True,
        )
    if adopted:
        return Kill(
            item,
            lane,
            SOFT,
            "hard kill asked over an adopted harness tree: stop the subagent and release the "
            "lease, but never discard the harness's own tree, still in use; the worktree is kept",
            False,
            True,
        )
    if salvageable is None:
        return Kill(
            item,
            lane,
            SOFT,
            "hard kill asked, the worktree could not be measured: stop the subagent and release "
            "the lease, but never guess it discardable; the worktree is kept",
            False,
            True,
        )
    if salvageable:
        return Kill(
            item,
            lane,
            HARD,
            "hard kill: stop the subagent, release the lease, and discard the worktree with "
            "its uncommitted work; the work is thrown away on purpose",
            True,
            False,
        )
    return Kill(
        item,
        lane,
        HARD,
        "hard kill: stop the subagent, release the lease, and discard a clean worktree; "
        "nothing is lost",
        True,
        False,
    )


def decisions(rows, *, adopted_items: dict[str, bool] | None = None) -> list[Kill]:
    """``rows`` of ``{item, lane, hard, adopted, salvageable}`` -> their switch.

    ``adopted_items`` lets a caller pass the adopted flag by item once; the row-dispatch's
    ``adopted`` resolution is `recover_auto.adopted_flag`, shared so the two lane decisions
    keep one copy of it.
    """
    out: list[Kill] = []
    for r in rows:
        out.append(
            decide(
                r["item"],
                r.get("lane", ""),
                hard=bool(r.get("hard")),
                adopted=adopted_flag(r, adopted_items),
                salvageable=r.get("salvageable"),
            )
        )
    return out
