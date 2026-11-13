"""Event kinds declared as data: owner, doc, retention, payload version, field schema,
redaction and the duplicate-check classification (SE-event-registry, LK-events-autodiscover).

One TOML file per OWNER (the subsystem that writes and folds the kinds), in
`ddflow/templates/events/<owner>.toml`, one ``[[kind]]`` table per kind::

    [[kind]]
    name = "lease.waiting"
    doc = "One sentence ending in a full stop: what the event records."
    retention = "keep"      # optional: keep (default) or compact
    v = 1                   # optional: the payload version; must equal `upcasters`'
    fields = { holder = ["str"], on = ["list"] }          # each field's observed JSON types
    text = ["reason"]       # free-text fields the committed log redacts (core.redact.log)
    lookup = { holder = "an agent id compared" }          # a field looked up or compared
    checked = "items.task_add"                            # OR exempt = "why it is not checked"

A kind declares everything about itself, so adding one edits only this file and its handler:
the registry `EVENT_KINDS`, the `tests/fixtures/event_kinds.json` snapshot, the redaction
tables `core.redact.log.LOG_TEXT_FIELDS` / `LOG_VERBATIM_FIELDS` and the coherence
classification of `test_coherence_coverage.py` are all DERIVED from these declarations
(field schema, `text`/`lookup`, `checked`/`exempt`). A new phase adds its kinds as a new
file or a new table and edits neither this module nor `core/events.py`, `core/redact/log.py`
nor the tests. The upcasters stay in `core.upcasters`.

Declaring a kind changes no behaviour: the fold is still `core.model.HANDLERS`.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .registry import Registry

EVENTS_DIR = Path(__file__).resolve().parent.parent / "templates" / "events"
#: How long a kind's events must be kept: forever, or foldable into a summary.
RETENTIONS = ("keep", "compact")
#: The JSON types a payload field may be written as; the snapshot lists them per field.
TYPES = ("str", "int", "float", "bool", "null", "list", "dict")
_KEYS = {"name", "doc", "retention", "v", "fields", "text", "lookup", "checked", "exempt"}


class EventKindError(ValueError):
    """A declaration that is not an event kind."""


@dataclass(frozen=True)
class EventKind:
    name: str
    owner: str
    doc: str
    retention: str = "keep"
    v: int = 1
    #: field name -> the JSON types it has been written with (the snapshot's row).
    fields: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: The field names that hold free text: redacted whole, or leaf by leaf (core.redact.log).
    text: tuple[str, ...] = ()
    #: field name -> why it is looked up or compared and kept byte-for-byte.
    lookup: Mapping[str, str] = field(default_factory=dict)
    #: The API add function that duplicate-checks this kind, or "" when it is exempt.
    checked: str = ""
    #: Why the kind files no checkable text (the coherence exemption), or "".
    exempt: str = ""


def _field_types(where: str, name: str, fname: str, ftypes: object) -> tuple[str, ...]:
    """The field's declared JSON types, or refuse a name outside `TYPES`."""
    if not isinstance(ftypes, list) or not ftypes or not all(t in TYPES for t in ftypes):
        raise EventKindError(f"{where}: {name}: field {fname!r} types must name {TYPES}")
    return tuple(ftypes)


def _parse_fields(where: str, name: str, row: Mapping[str, Any]) -> Mapping[str, tuple[str, ...]]:
    """The kind's field schema (`fields = { name = ["str", ...] }`) as name -> types."""
    if "fields" not in row:
        return {}
    raw = row["fields"]
    if not isinstance(raw, dict):
        raise EventKindError(f"{where}: {name}: fields must be a table")
    out: dict[str, tuple[str, ...]] = {}
    for fname, ftypes in raw.items():
        if not isinstance(fname, str) or not fname:
            raise EventKindError(f"{where}: {name}: a field name must be a string")
        out[fname] = _field_types(where, name, fname, ftypes)
    return out


def _parse_lookup(where: str, name: str, row: Mapping[str, Any]) -> Mapping[str, str]:
    """The kind's verbatim fields (`lookup = { name = "why" }`) as name -> why."""
    if "lookup" not in row:
        return {}
    raw = row["lookup"]
    if not isinstance(raw, dict):
        raise EventKindError(f"{where}: {name}: lookup must be a table")
    out: dict[str, str] = {}
    for fname, why in raw.items():
        if not isinstance(fname, str) or not isinstance(why, str):
            raise EventKindError(f"{where}: {name}: lookup maps a field to its reason")
        out[fname] = why
    return out


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
    return [_parse_row(where, owner, n, row) for n, row in enumerate(rows, 1)]


def _parse_row(where: str, owner: str, n: int, row: Mapping[str, Any]) -> EventKind:
    """One `[[kind]]` row, or refuse a row that is not a table / holds an unknown key."""
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
    text = row.get("text", [])
    if not isinstance(text, list) or not all(isinstance(t, str) for t in text):
        raise EventKindError(f"{where}: {name}: text must be an array of field names")
    checked, exempt = row.get("checked"), row.get("exempt")
    if checked is not None and exempt is not None:
        raise EventKindError(f"{where}: {name}: checked and exempt are mutually exclusive")
    if checked is not None and not isinstance(checked, str):
        raise EventKindError(f"{where}: {name}: checked must be a string")
    if exempt is not None and not isinstance(exempt, str):
        raise EventKindError(f"{where}: {name}: exempt must be a string")
    return EventKind(
        name,
        owner,
        doc,
        str(row.get("retention", "keep")),
        v,
        fields=_parse_fields(where, name, row),
        text=tuple(text),
        lookup=_parse_lookup(where, name, row),
        checked=checked or "",
        exempt=exempt or "",
    )


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
    from .upcasters import current_version  # deferred: upcasters imports core.events

    out: list[str] = []
    seen: dict[str, str] = {}
    for k in declared:
        if k.name in seen:
            out.append(f"{k.name}: declared twice, by {seen[k.name]} and {k.owner}")
        seen[k.name] = k.owner
        if " " not in k.doc.strip() or not k.doc.strip().endswith("."):
            out.append(
                f"{k.name}: doc must be a sentence (two words, a full stop; owner {k.owner})"
            )
        if "|" in k.doc or k.doc != k.doc.strip() or any(c in k.doc for c in "\r\n"):
            out.append(f"{k.name}: doc must be one trimmed line with no '|' (it is a table cell)")
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


# -- the derived surfaces ----------------------------------------------------------------
#: Adding a kind edits only its declaration (and its handler): these functions are what the
#: snapshot, the redaction tables and the coherence classification are generated FROM, so a
#: shared file is never the place a new kind is registered.


def field_schema(declared: Iterable[EventKind]) -> dict:
    """`tests/fixtures/event_kinds.json` as the declarations make it: kind -> its payload
    version and per-field JSON types. The committed snapshot IS this structure."""
    return {
        "format": 1,
        "kinds": {
            k.name: {"v": k.v, "fields": {f: list(t) for f, t in sorted(k.fields.items())}}
            for k in sorted(declared, key=lambda k: k.name)
        },
    }


def text_fields(declared: Iterable[EventKind]) -> Mapping[str, tuple[str, ...]]:
    """`core.redact.log.LOG_TEXT_FIELDS`: kind -> its free-text fields, in the declared order."""
    return {k.name: k.text for k in sorted(declared, key=lambda k: k.name) if k.text}


def verbatim_fields(declared: Iterable[EventKind]) -> Mapping[tuple[str, str], str]:
    """`core.redact.log.LOG_VERBATIM_FIELDS`: (kind, field) -> why it stays byte-for-byte."""
    out: dict[tuple[str, str], str] = {}
    for k in sorted(declared, key=lambda k: k.name):
        for f, why in sorted(k.lookup.items()):
            out[(k.name, f)] = why
    return out


def checked(declared: Iterable[EventKind]) -> Mapping[str, str]:
    """The duplicate-checked kinds: kind -> the API add function that runs the check."""
    return {k.name: k.checked for k in sorted(declared, key=lambda k: k.name) if k.checked}


def exempt(declared: Iterable[EventKind]) -> Mapping[str, str]:
    """The kinds the duplicate check does not cover: kind -> why it files no checkable text
    (the coherence test pins the reason to over 20 characters)."""
    return {k.name: k.exempt for k in sorted(declared, key=lambda k: k.name) if k.exempt}


__all__: list[Any] = [
    "EVENT_KINDS", "EventKind", "EventKindError", "checked", "exempt", "field_schema",
    "load_dir", "owners", "parse", "problems", "table", "text_fields", "verbatim_fields",
]  # fmt: skip
