"""Recover-auto: what a stopped lane's leftovers let ddflow settle alone.

The self-recovery half of the lanes watchdog. A lane that stopped holding an EXPIRED lease
over SALVAGEABLE work is the one corpse the machine can revive by itself: file a resume item
or re-lease the item with a marker + rationale, and record the recovered unit -- nothing is
lost. Everything else needs a person or a new agent, and none of it may be auto-applied:

* an ADOPTED harness tree is the harness's own working tree, still in use -- never touched;
* a gate only the OPERATOR answers is never auto-applied (ddflow cannot settle it alone);
* a tree ddflow could not measure is never guessed clean -- nothing automatic.

Pure: no git, no clock, no I/O. ``salvageable`` is measured upstream the same way
``leases.recovery`` measures it (True = work held, False = clean, None = unmeasurable), so
the sheet and any apply path read one answer, testable without a repository.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

#: Recover-auto verdicts, most automatic first.
AUTO_RESUME = "auto_file_resume"  # file a resume item / re-lease with a marker; work kept
EXPIRE = "record_expiry"  # the tree is clean: record the lease expired, nothing to lose
STAY = "stay"  # needs a person: adopted tree, operator-answer gate, or unmeasurable work

EFFECTS = (AUTO_RESUME, EXPIRE, STAY)

#: The mark an auto recovery leaves on the unit it recorded, so a re-lease stands out.
MARKER = "ddflow:watchdog:auto"


@dataclass(frozen=True)
class RecoverAuto:
    """One stopped lane's recovery, as a pure decision."""

    item: str
    lane: str
    effect: str
    rationale: str
    auto: bool
    marker: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide(
    item: str,
    lane: str,
    *,
    salvageable: bool | None,
    adopted: bool = False,
    operator_answer: bool = False,
) -> RecoverAuto:
    """The verdict for a lane that stopped with an expired lease.

    Guardrails first -- neither an adopted tree nor an operator-answer gate is ever
    settled alone -- then the measured work: salvageable work is auto-resumed, a clean
    tree is auto-expired, and unmeasurable work is left for a person.
    """
    if adopted:
        return RecoverAuto(
            item,
            lane,
            STAY,
            "adopted harness tree: the harness's own, still in use; leave it to the harness "
            "and never auto-apply an adopted tree",
            False,
        )
    if operator_answer:
        return RecoverAuto(
            item,
            lane,
            STAY,
            "needs an operator-answer gate: ddflow cannot settle it alone; a person answers first",
            False,
        )
    if salvageable is None:
        return RecoverAuto(
            item,
            lane,
            STAY,
            "the worktree could not be measured; never guessed clean, nothing automatic",
            False,
        )
    if salvageable is True:
        return RecoverAuto(
            item,
            lane,
            AUTO_RESUME,
            "expired lease over a salvageable worktree: ddflow alone files a resume item and "
            "re-leases it with a marker, recording the recovered unit; the work is kept",
            True,
            MARKER,
        )
    return RecoverAuto(
        item,
        lane,
        EXPIRE,
        "clean worktree on an expired lease: record the lease expired; the claim is freed and "
        "nothing is lost",
        True,
        MARKER,
    )


def decisions(rows, *, adopted_items: dict[str, bool] | None = None) -> list[RecoverAuto]:
    """``rows`` of ``{item, lane, salvageable, adopted, operator_answer}`` -> their verdicts.

    ``adopted_items`` lets a caller pass the adopted flag by item once; a row's own
    ``adopted`` wins where both exist.
    """
    out: list[RecoverAuto] = []
    adopted_items = adopted_items or {}
    for r in rows:
        if "adopted" in r:
            # A row's own flag wins even when it is falsy: measured not-adopted is a
            # real answer, not an omission to paper over with the caller's fallback.
            adopted = bool(r["adopted"])
        else:
            adopted = bool(adopted_items.get(r["item"]))
        out.append(
            decide(
                r["item"],
                r.get("lane", ""),
                salvageable=r.get("salvageable"),
                adopted=adopted,
                operator_answer=bool(r.get("operator_answer")),
            )
        )
    return out
