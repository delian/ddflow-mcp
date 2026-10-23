"""The replay round trip (D-no-deletion): rebuilding from the log must give the live state,
and nothing the log once recorded may disappear.

`ddflow replay --verify` runs `check`. Closing, abandoning, folding, superseding and
forgetting are state changes; a record present after N events must still be present after
N+k. A fold that drops one is the bug this finds, whether it is a handler deleting a key or
a command removing an event."""

from __future__ import annotations

from dataclasses import fields

from ..core.events import Event
from ..core.model import fold
from ..core.records import State

#: The State collections whose keys are records (a key that goes missing is a deletion).
RECORD_COLLECTIONS = (
    "items",
    "bugs",
    "lessons",
    "research",
    "decisions",
    "memories",
    "jobs",
    "sessions",
)

#: How many prefixes `check` folds by default: each costs one fold of the log.
CHECKPOINTS = 16


def record_ids(st: State) -> dict[str, frozenset[str]]:
    """Collection -> the ids of the records in it."""
    return {name: frozenset(getattr(st, name)) for name in RECORD_COLLECTIONS}


def differing_fields(a: State, b: State) -> list[str]:
    """The State fields on which two folds disagree."""
    return [f.name for f in fields(State) if getattr(a, f.name) != getattr(b, f.name)]


def _prefix_ends(n: int, checkpoints: int) -> list[int]:
    if checkpoints <= 0 or n <= 1:
        return []
    step = max(1, n // checkpoints)
    return sorted({*range(step, n, step)})


def check(events: list[Event], live: State, *, checkpoints: int = CHECKPOINTS) -> list[str]:
    """Problems found replaying `events` from scratch against the `live` folded state.

    1. the from-scratch fold equals `live` (every State field);
    2. no event id appears twice (a replay would apply it twice);
    3. at `checkpoints` prefixes of the log, every record id present is still present in
       the full fold: nothing recorded was later removed from the state.
    """
    problems: list[str] = []
    rebuilt = fold(events, strict=False)
    for name in differing_fields(rebuilt, live):
        problems.append(f"rebuilt state differs from the live state in {name!r}")
    seen: set[str] = set()
    for ev in events:
        if ev.id and ev.id in seen:
            problems.append(f"event id {ev.id} occurs twice in the log")
        seen.add(ev.id)
    final = record_ids(rebuilt)
    for end in _prefix_ends(len(events), checkpoints):
        earlier = record_ids(fold(events[:end], strict=False))
        for name, ids in earlier.items():
            for gone in sorted(ids - final[name]):
                problems.append(
                    f"{name[:-1] if name.endswith('s') else name} {gone} was in the state after "
                    f"{end} events and is gone from the full replay"
                )
    return problems
