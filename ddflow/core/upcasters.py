"""Event evolution: per-kind payload versions and the upcasters that read old ones (B-uni-compat-events).

The log is append-only and is never rewritten (D-compat, R-compat). When the shape of an
event's ``data`` has to change -- a field renamed, removed or retyped, a kind renamed --
the kind's payload version goes up by one and a pure UPCASTER is registered that turns a
payload of the previous version into the new shape. The fold applies the chain when it
reads an event, so every handler only ever sees the current shape, and the events of every
released ddflow keep folding (Greg Young, "Versioning in an Event Sourced System": weak
schema plus upcasting).

- ``data["v"]`` carries the payload version. It is absent at version 1, so every event
  written before this module existed is version 1 and none of them changes.
- `PAYLOAD_VERSIONS` declares each kind's current version; a kind not listed is at 1.
- `UPCASTERS[(kind, v)]` maps a version-``v`` payload to version ``v + 1``. It may also
  rename the kind (it returns the kind too): the payload it returns is then at the version
  it carries of the NEW kind (1 when unstamped), and the chain continues from there.
- A payload NEWER than this code knows is never guessed at: `upcast` raises
  `NewerPayload`, which the fold counts like an unknown kind (D-compat 2: an older clone
  preserves what it does not understand).

An addition -- a new kind, a new optional field -- needs neither: readers ignore what they
do not know. `tests/test_event_upcasters.py` holds the committed snapshot of every kind's
fields and types (`tests/fixtures/event_kinds.json`) and refuses a removed kind, a removed
field or a changed type unless a version bump with its upcaster is registered here and the
compatibility contract (docs/ddflow/compatibility.md) names it.

Pure: no I/O. The event's ``id`` is kept through an upcast -- it is the hash of what was
written, and every reference to the event (adoptions, contest resolutions) cites it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from .events import Event

#: The payload-version key in an event's ``data``. Absent means 1.
VERSION_KEY = "v"

#: kind -> its current payload version, for every kind above 1. Empty while no payload has
#: changed shape: every kind is at version 1.
PAYLOAD_VERSIONS: dict[str, int] = {}

#: An upcaster: (kind, version-v payload) -> (kind, version-v+1 payload). Pure; it must
#: not mutate its input. One that RENAMES the kind returns a payload of the new kind, at the
#: version its ``v`` says (absent: 1) -- so it sets or drops ``v``, never copies the old one.
Upcaster = Callable[[str, Mapping[str, Any]], tuple[str, dict[str, Any]]]

#: (kind, v) -> the upcaster from version v to v + 1 of that kind.
UPCASTERS: dict[tuple[str, int], Upcaster] = {}


class NewerPayload(ValueError):
    """An event whose payload version is above what this code knows: written by a newer
    ddflow. Never guessed at; the fold counts it as skipped (or raises when strict)."""


def current_version(kind: str, versions: Mapping[str, int] | None = None) -> int:
    """The payload version this code writes and folds for `kind`."""
    return int((PAYLOAD_VERSIONS if versions is None else versions).get(kind, 1))


def version_of(data: Mapping[str, Any]) -> int:
    """The payload version an event was written at (1 when unstamped). A value that is
    not a positive integer is malformed and raises ValueError -- the fold records it as
    a problem with that one event."""
    raw = data.get(VERSION_KEY, 1)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(f"payload version {raw!r} is not a positive integer")
    return raw


def stamp(kind: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """`data` as a writer must append it: with ``v`` set when `kind` is above version 1.

    A version-1 payload stays unstamped, so a log written while nothing has changed shape
    is byte-identical to one written before payload versions existed."""
    out = dict(data)
    version = current_version(kind)
    if version > 1:
        out[VERSION_KEY] = version
    return out


def upcast(
    ev: Event,
    *,
    versions: Mapping[str, int] | None = None,
    upcasters: Mapping[tuple[str, int], Upcaster] | None = None,
) -> Event:
    """`ev` in the current shape of its kind: every registered upcaster from the version it
    was written at applied in turn. The same object when nothing applies (the common case,
    and why the fold pays almost nothing for this). Raises `NewerPayload` for a payload
    newer than this code, ValueError for a malformed version or a missing upcaster step.

    `versions` and `upcasters` default to the module registries; tests pass their own."""
    versions = PAYLOAD_VERSIONS if versions is None else versions
    upcasters = UPCASTERS if upcasters is None else upcasters
    kind, data = ev.kind, ev.data
    v = version_of(data)
    target = current_version(kind, versions)
    if v == target:
        return ev
    if v > target:
        raise NewerPayload(f"event {ev.kind!r} has payload version {v}; this code knows {target}")
    seen: set[tuple[str, int]] = set()
    while v < target:
        step = upcasters.get((kind, v))
        if step is None:
            raise ValueError(f"no upcaster for {kind!r} payload version {v} -> {v + 1}")
        if (kind, v) in seen:  # a rename cycle would otherwise never end
            raise ValueError(f"upcaster cycle at {kind!r} version {v}")
        seen.add((kind, v))
        new_kind, new = step(kind, data)
        data = dict(new)
        if new_kind == kind:
            data[VERSION_KEY] = v + 1
        kind = new_kind  # a rename: `data` is at the version it carries of the new kind
        v = version_of(data)
        target = current_version(kind, versions)
        if v > target:
            raise NewerPayload(f"upcast {ev.kind!r} reached {kind!r} version {v} > {target}")
    if data.get(VERSION_KEY) == 1:
        data = {k: val for k, val in data.items() if k != VERSION_KEY}
    return replace(ev, kind=kind, data=data)
