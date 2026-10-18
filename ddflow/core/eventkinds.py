"""Event kinds declared as data: owner, doc, retention and payload version (SE-event-registry).

One TOML file per OWNER (the subsystem that writes and folds the kinds), in
`ddflow/templates/events/<owner>.toml`, one ``[[kind]]`` table per kind::

    [[kind]]
    name = "lease.waiting"
    doc = "One sentence: what the event records."
    retention = "keep"      # optional: keep (default) or compact
    v = 1                   # optional: the payload version; must equal `upcasters`'

The registry is `EVENT_KINDS`. `problems()` is the ratchet: every kind the fold handles is
declared exactly once, every declaration has a handler, a doc, a known retention and the
payload version `core.upcasters` writes. A new phase adds its kinds as a new file or a new
table and edits neither this module nor `core/events.py`. The field schema stays in
`tests/fixtures/event_kinds.json` (the snapshot the compatibility ratchet compares); the
upcasters stay in `core.upcasters`; the redaction tables in `core.redact`.

Declaring a kind changes no behaviour: the fold is still `core.model.HANDLERS`.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import Registry

EVENTS_DIR = Path(__file__).resolve().parent.parent / "templates" / "events"
#: How long a kind's events must be kept: forever, or foldable into a summary.
RETENTIONS = ("keep", "compact")
_KEYS = {"name", "doc", "retention", "v"}
MIN_DOC = 10


class EventKindError(ValueError):
    """A declaration that is not an event kind."""


@dataclass(frozen=True)
class EventKind:
    name: str
    owner: str
    doc: str
    retention: str = "keep"
    v: int = 1


def parse(text: str, *, owner: str, source: str = "") -> list[EventKind]:
    """The kinds one owner's file declares; a malformed table names the file and the kind."""
    where = source or owner
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise EventKindError(f"{where}: not TOML ({exc})") from exc
    if bad := sorted(set(raw) - {"kind"}):
        raise EventKindError(f"{where}: unknown top-level key(s) {', '.join(bad)}")
    rows = raw.get("kind", [])
    if not isinstance(rows, list):
        raise EventKindError(f"{where}: kind must be an array of [[kind]] tables")
    out: list[EventKind] = []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise EventKindError(f"{where}: kind #{n} must be a table")
        if extra := sorted(set(row) - _KEYS):
            raise EventKindError(f"{where}: kind #{n}: unknown key(s) {', '.join(extra)}")
        name, doc = row.get("name"), row.get("doc")
        if not isinstance(name, str) or not name:
            raise EventKindError(f"{where}: kind #{n} needs a name")
        if not isinstance(doc, str):
            raise EventKindError(f"{where}: {name}: doc must be a string")
        v = row.get("v", 1)
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise EventKindError(f"{where}: {name}: v must be a positive integer")
        out.append(EventKind(name, owner, doc, str(row.get("retention", "keep")), v))
    return out


def load_dir(path: Path = EVENTS_DIR) -> list[EventKind]:
    """Every kind declared under `path`, by owner file then order; a file is read whole or
    refused, so a registry never holds half an owner."""
    out: list[EventKind] = []
    for entry in sorted(path.glob("*.toml")):
        out += parse(entry.read_text(encoding="utf-8"), owner=entry.stem, source=entry.name)
    return out


EVENT_KINDS: Registry[EventKind] = Registry("event kind", key=lambda k: k.name, loader=load_dir)


def problems(
    declared: Iterable[EventKind],
    handled: Iterable[str],
    versions: Mapping[str, int] | None = None,
) -> list[str]:
    """What is wrong with the declarations against the kinds the fold `handled`, one line
    each; empty when the registry and the fold agree."""
    from .upcasters import current_version

    out: list[str] = []
    seen: dict[str, str] = {}
    for k in declared:
        if k.name in seen:
            out.append(f"{k.name}: declared twice, by {seen[k.name]} and {k.owner}")
        seen[k.name] = k.owner
        if len(k.doc.strip()) < MIN_DOC:
            out.append(f"{k.name}: doc is missing or too short (owner {k.owner})")
        if k.retention not in RETENTIONS:
            out.append(f"{k.name}: retention {k.retention!r} is not one of {RETENTIONS}")
        want = current_version(k.name, versions)
        if k.v != want:
            out.append(f"{k.name}: declared at payload v{k.v}, upcasters write v{want}")
    handled = set(handled)
    out += [
        f"{n}: handled by the fold but declared by no owner" for n in sorted(handled - set(seen))
    ]
    out += [f"{n}: declared by {seen[n]} but nothing folds it" for n in sorted(set(seen) - handled)]
    return out


def table(declared: Iterable[EventKind]) -> str:
    """The declarations as a Markdown table, for the generated documentation."""
    rows = sorted(declared, key=lambda k: (k.owner, k.name))
    lines = ["| kind | owner | v | retention | what it records |", "|---|---|---|---|---|"]
    lines += [f"| `{k.name}` | {k.owner} | {k.v} | {k.retention} | {k.doc} |" for k in rows]
    return "\n".join(lines) + "\n"


def owners(declared: Iterable[EventKind]) -> Mapping[str, list[str]]:
    """owner -> its kind names."""
    out: dict[str, list[str]] = {}
    for k in declared:
        out.setdefault(k.owner, []).append(k.name)
    return {o: sorted(ns) for o, ns in sorted(out.items())}


__all__: list[Any] = [
    "EVENT_KINDS", "EventKind", "EventKindError", "load_dir", "owners", "parse", "problems", "table",
]  # fmt: skip
