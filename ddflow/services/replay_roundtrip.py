"""The replay round trip (D-no-deletion): rebuilding from the log must give the live state,
and nothing the log once recorded may disappear.

`ddflow replay --verify` runs `check`. Closing, abandoning, folding, superseding and
forgetting are state changes; a record present after N events must still be present after
every later event. A fold that drops one is the bug this finds, whether a handler deletes a
key or a command removes an event from the log."""

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


def differing_fields(a: State, b: State) -> list[str]:
    """The State fields on which two folds disagree."""
    return [f.name for f in fields(State) if getattr(a, f.name) != getattr(b, f.name)]


def vanished(events: list[Event]) -> list[str]:
    """Every record that was in the state after some event and not after a later one.

    One fold, observed after each event, so it is exact: a record removed and re-created
    under the same id, or one that lives only between two events, is reported at the event
    that removed it. A record is reported once per removal.
    """
    ever: dict[str, set[str]] = {name: set() for name in RECORD_COLLECTIONS}
    gone: list[str] = []

    def observe(st: State, ev: Event) -> None:
        for name, seen in ever.items():
            now = getattr(st, name).keys()
            for rid in sorted(seen - now):  # a swap (one out, one in) keeps the length
                gone.append(f"{rid} disappeared from {name} at {ev.kind} {ev.id}")
            seen.clear()
            seen.update(now)

    fold(events, strict=False, observe=observe)
    return gone


def check(events: list[Event], live: State, *, from_bytes: list[Event] | None = None) -> list[str]:
    """Problems found replaying `events` from scratch against the `live` folded state.

    1. the from-scratch fold equals `live` (every State field);
    2. no event id appears twice (a replay would apply it twice);
    3. no record disappears at any event (`vanished`);
    4. given `from_bytes` (the log re-read from its bytes, not its snapshot), every event id
       there is in `events`: a snapshot that lost an event is a deletion nobody asked for.
    """
    problems: list[str] = []
    for name in differing_fields(fold(events, strict=False), live):
        problems.append(f"rebuilt state differs from the live state in {name!r}")
    seen: set[str] = set()
    for ev in events:
        if ev.id and ev.id in seen:
            problems.append(f"event id {ev.id} occurs twice in the log")
        seen.add(ev.id)
    problems += vanished(events)
    if from_bytes is not None:
        for ev in from_bytes:
            if ev.id not in seen:
                problems.append(
                    f"event {ev.kind} {ev.id} is in the log's bytes and not in its read"
                )
    return problems
