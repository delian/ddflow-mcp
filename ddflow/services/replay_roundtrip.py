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

#: The State collections whose keys are records (a key that goes missing is a deletion); `defs`
#: holds the managed definitions (doc types, schedules, triggers, skills, agents, rules).
RECORD_COLLECTIONS = (
    "items",
    "bugs",
    "lessons",
    "research",
    "decisions",
    "memories",
    "jobs",
    "sessions",
    "defs",
)


def differing_fields(a: State, b: State) -> list[str]:
    """The State fields on which two folds disagree."""
    return [f.name for f in fields(State) if getattr(a, f.name) != getattr(b, f.name)]


def scan(events: list[Event]) -> tuple[list[str], State]:
    """One fold of `events`, observed after each event: the records that disappeared, and
    the state it ended in.

    Exact: a record removed and re-created under the same id, or one that lives only between
    two events, is reported at the event that removed it. A record is reported once per
    removal.
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

    return gone, fold(events, strict=False, observe=observe)


def vanished(events: list[Event]) -> list[str]:
    """Every record that was in the state after some event and not after a later one."""
    return scan(events)[0]


def check(events: list[Event], live: State, *, from_bytes: list[Event] | None = None) -> list[str]:
    """Problems found replaying the log from scratch against the `live` folded state.

    `events` is the log as the commands read it (snapshot and caches); `from_bytes` is the
    log re-read from its bytes alone, and is the from-scratch replay when given (else
    `events` is). With it:

    1. the from-scratch fold equals `live` (every State field);
    2. no event id appears twice in the replay (it would be applied twice);
    3. no record disappears at any event (`scan`);
    4. every event id in the replay is in `events`: a snapshot that lost an event is a
       deletion nobody asked for.
    """
    scratch = events if from_bytes is None else from_bytes
    gone, rebuilt = scan(scratch)
    problems = [
        f"rebuilt state differs from the live state in {name!r}"
        for name in differing_fields(rebuilt, live)
    ]
    seen: set[str] = set()
    for ev in scratch:
        if ev.id and ev.id in seen:
            problems.append(f"event id {ev.id} occurs twice in the log")
        seen.add(ev.id)
    problems += gone
    have = {ev.id for ev in events}
    problems += [
        f"event {ev.kind} {ev.id} is in the log's bytes and not in its read"
        for ev in scratch
        if ev.id not in have
    ]
    return problems
