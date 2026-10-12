"""Concrete moves for the coordinator. Printed, never run: this module has no side effect."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from ...core.flowcontrol import Decision
from . import states as S
from .ledger import Lane

#: Urgency order of the kinds: lost work first, then stalls, then spare capacity.
KINDS = ("resume", "stall", "idle", "over-limit")


@dataclass(frozen=True)
class Move:
    kind: str
    text: str
    lanes: tuple[str, ...] = ()
    command: str = ""  #: the line the operator or a Routine runs; never run here

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["lanes"] = list(self.lanes)
        return d


def _resume(lane: Lane) -> Move:
    why = "reason unknown" if lane.state == S.STOPPED_UNKNOWN else "its session ended"
    return Move(
        "resume",
        f"lane {lane.name} stopped ({why}) with {lane.item} unfinished"
        + (f" in {lane.worktree}" if lane.worktree else "")
        + "; no harness descriptor carries a spawn line yet, so start a session yourself and "
        "give it the lane brief as its first prompt",
        (lane.name,),
        f"ddflow lanes brief {lane.name}",
    )


def _stalls(lanes: Sequence[Lane]) -> list[Move]:
    """One move per blocked item, naming the lane that holds what it needs."""
    seen: dict[str, tuple[str, str, str]] = {}
    for ln in lanes:
        for s in ln.stalls:
            seen.setdefault(s.item, (s.holder, s.held_item, s.detail))
    out = []
    for item, (holder, held_item, detail) in sorted(seen.items()):
        waiting = tuple(ln.name for ln in lanes if any(s.item == item for s in ln.stalls))
        who = f"lane {holder}" if holder else "a lease"
        out.append(
            Move(
                "stall",
                f"{item} is blocked by {who} ({held_item}: {detail}); idle: {', '.join(waiting)}."
                f" Ask {holder or 'the holder'} to finish, narrow its globs, or release",
                waiting,
                f"ddflow wait --item {item}",
            )
        )
    return out


def _idle(lanes: Sequence[Lane]) -> list[Move]:
    ready = next((ln.ready for ln in lanes if ln.state == S.IDLE_READY), ())
    names = tuple(ln.name for ln in lanes if ln.state == S.IDLE_READY)
    if not names:
        return []
    shown = ", ".join(ready[:5])
    return [
        Move(
            "idle",
            f"{len(names)} lane(s) idle while {len(ready)} item(s) are ready ({shown}): "
            f"{', '.join(names)}",
            names,
            "ddflow next",
        )
    ]


def _over(lanes: Sequence[Lane], limit: Decision | None) -> list[Move]:
    if limit is None:
        return []
    active = [ln.name for ln in lanes if ln.state in S.ACTIVE]
    if len(active) <= limit.limit:
        return []
    return [
        Move(
            "over-limit",
            f"{len(active)} lanes are working but the parallelism limit is {limit.limit} "
            f"({limit.mode}; {limit.reason}): do not start another, let one finish",
            tuple(active),
        )
    ]


def advise(lanes: Sequence[Lane], limit: Decision | None, *, max_moves: int) -> list[Move]:
    """The moves, most urgent first, at most ``max_moves``."""
    moves = [_resume(ln) for ln in lanes if ln.state in S.UNFINISHED]
    moves += _stalls(lanes) + _idle(lanes) + _over(lanes, limit)
    return moves[: max(0, max_moves)]
