"""The MCP resources a `RecordKind` serves (RC4), derived from its descriptor.

A kind whose records are stored definitions and that says ``resource`` gets the list URI
``ddflow://records/<name>`` and the per-record ``ddflow://records/<name>/<id>``, both
redacted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core import provenance as PV
from ...core.textcut import clip
from ...services.export.safe import redact_text
from .._base import _load
from ._kind import KINDS, RecordKind
from ._verbs import _exists, _records

#: A resource URI is ``ddflow://records/<kind>`` (the list) or ``.../<kind>/<id>`` (one).
RESOURCE_PREFIX = "ddflow://records/"
#: Rows the list resource shows (newest first), and characters of one field in either.
RESOURCE_ROWS = 50
RESOURCE_TEXT = 600


def _resource_kinds() -> dict[str, RecordKind]:
    return {k.name: k for k in KINDS.values() if k.resource and k.stored_kind}


def record_resource_listing() -> list[dict[str, str]]:
    """What ``resources/list`` says of each kind that is served as a resource: the list URI,
    with the pattern of the per-record one in its description."""
    return [
        {
            "uri": f"{RESOURCE_PREFIX}{k.name}",
            "name": f"{k.name.capitalize()} records",
            "description": f"{k.summary[:1].upper()}{k.summary[1:]}. "
            f"Newest first; {RESOURCE_PREFIX}{k.name}/{{id}} reads one with its history.",
            "mimeType": "text/markdown",
        }
        for k in _resource_kinds().values()
    ]


def _resource_text(kind: RecordKind, rec: Any, full: bool) -> str:
    """One record as a line (``full`` False) or whole: its fields as DATA inside the fence,
    each cut to `RESOURCE_TEXT`, then its status, successor chain and history digest."""
    head = f"{rec.id}  [{rec.status}] {str(rec.fields.get(kind.title, ''))[:120]}".rstrip()
    if not full:
        return f"- {' '.join(head.split())}"
    body = "; ".join(
        f"{n}: {clip(' '.join(str(v).split()), RESOURCE_TEXT)}" for n, v in rec.fields.items()
    )
    origin = PV.Origin(PV.IMPORTED if rec.source else PV.AGENT, rec.by, rec.source)
    out = [f"# {kind.name} {rec.id}", f"status: {rec.status}"]
    if rec.successor:
        out.append(f"{rec.status} by: {rec.successor}")
    if rec.merged_from:
        out.append(f"absorbed: {', '.join(rec.merged_from)}")
    out.append(PV.fence(kind.name, rec.id, body, origin))
    events = [h["event"] for h in rec.history]
    out.append(
        f"history: {len(events)} entries, first {rec.at[:10]}, last {(rec.updated_at or rec.at)[:10]} "
        f"({', '.join(events[-5:])}); `ddflow {kind.name} history {rec.id} --diff` has them all"
    )
    return "\n".join(out)


def record_resource_read(repo: Path, uri: str, *, agent: str = "") -> str | None:
    """The text of a record resource, redacted, or None when ``uri`` names no served kind or
    record. The list is bounded (`RESOURCE_ROWS`, newest first) and says how many it holds."""
    kinds = _resource_kinds()
    if not uri.startswith(RESOURCE_PREFIX):
        return None
    name, _sep, rid = uri[len(RESOURCE_PREFIX) :].partition("/")
    kind = kinds.get(name)
    if kind is None:
        return None
    _log, cfg, st = _load(repo, agent)
    if rid:
        rec = _exists(st, kind, rid)
        return None if rec is None else redact_text(_resource_text(kind, rec, True), cfg).text
    recs = sorted(
        _records(st, kind, everything=True),
        key=lambda r: (r.updated_at or r.at, r.id),
        reverse=True,
    )
    lines = [
        f"# {kind.name} records",
        f"{len(recs)} in all; newest {min(len(recs), RESOURCE_ROWS)}:",
    ]
    lines += [_resource_text(kind, r, False) for r in recs[:RESOURCE_ROWS]]
    return redact_text("\n".join(lines), cfg).text
