"""A guidance file as the fields of a definition record (`core.defs`), and back.

Rules are definitions: the log holds each rule's content as ``def.recorded`` /
``def.updated`` fields (kind ``rule``) and ``.ddflow/rules/<id>.toml`` is a view rendered
from them (D-unify 7). This is the one mapping between the two, so the import, the write
path and the drift check cannot disagree about what a rule's content IS:

* `to_fields`: the record's CONTENT, every key always present (a digest must not depend on
  which defaults a file happens to spell out), plain JSON;
* `to_provenance`: what is NOT content -- when the file was created and last changed -- kept
  out of the fields so a re-save that changes only a timestamp is no change;
* `from_fields`: the record back, for rendering the file; `render` is `fileformat.render`
  of it, so a file the view writes is one `fileformat.parse` reads back to the same fields;
* `logged` and `digest_of`: the fields as the committed log holds them (the ``log``
  redaction profile) and their digest, which is what a ``def.*`` event's ``digest`` is.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...config import Config
from ...core import defs as D
from ...core import redact as R
from ..redact_report import redactor
from . import fileformat
from .record import ACCEPTED, GuidanceRecord, KindSpec, Scope

#: The keys of a record's content, in the order they are listed. The kind's own extras
#: (`KindSpec.extras`) follow, under their own names.
FIELDS = (
    "title",
    "body",
    "level",
    "tags",
    "priority",
    "globs",
    "categories",
    "gates",
    "category",
    "enforcement",
    "status",
    "owner",
    "review_by",
    "sources",
    "checks",
    "links",
)
#: What a file stamps and is not content.
STAMPS = ("created", "updated")


def to_fields(rec: GuidanceRecord, spec: KindSpec) -> dict[str, Any]:
    """The content of ``rec`` as plain JSON, every key present."""
    fields: dict[str, Any] = {
        "title": rec.title,
        "body": rec.body,
        "level": rec.level,
        "tags": list(rec.tags),
        "priority": rec.priority,
        "globs": list(rec.scope.globs),
        "categories": list(rec.scope.categories),
        "gates": list(rec.scope.gates),
        "category": rec.category,
        "enforcement": rec.enforcement,
        "status": rec.status,
        "owner": rec.owner,
        "review_by": rec.review_by,
        "sources": list(rec.sources),
        "checks": [dict(c) for c in rec.checks],
        "links": list(rec.links),
    }
    for key, default in spec.extras:
        fields[key] = rec.ext.get(key, default)
    return fields


def _iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def to_provenance(rec: GuidanceRecord) -> dict[str, str]:
    """When the file was created and last changed, as ISO text ({} for a kind with no stamps)."""
    return {k: _iso(rec.provenance[k]) for k in STAMPS if rec.provenance.get(k)}


def from_fields(
    rid: str, fields: dict[str, Any], provenance: dict[str, Any], spec: KindSpec
) -> GuidanceRecord:
    """The record a definition's fields and stamps describe. A key the fields lack takes the
    kind's default, so a definition recorded by an older ddflow still renders."""

    def get(key: str, default: Any = "") -> Any:
        """The field, or ``default`` when it is absent or null (nothing is "None" text)."""
        value = fields.get(key)
        return default if value is None else value

    return GuidanceRecord(
        id=rid,
        kind=spec.kind,
        title=str(get("title")),
        body=str(get("body")),
        scope=Scope(
            globs=tuple(get("globs", ())),
            categories=tuple(get("categories", ())),
            gates=tuple(get("gates", ())),
        ),
        level=str(get("level", spec.default_level)),
        category=str(get("category")),
        tags=list(get("tags", [])),
        priority=int(get("priority", spec.default_priority)),
        enforcement=str(get("enforcement", spec.default_enforcement)),
        status=str(get("status", ACCEPTED)),  # as `fileformat.parse`: a file has no other default
        owner=str(get("owner")),
        review_by=str(get("review_by")),
        sources=list(get("sources", [])),
        checks=[dict(c) for c in get("checks", [])],
        links=list(get("links", [])),
        provenance={k: provenance[k] for k in STAMPS if provenance.get(k)} if spec.stamped else {},
        ext={k: get(k, d) for k, d in spec.extras},
    )


def render(rid: str, fields: dict[str, Any], provenance: dict[str, Any], spec: KindSpec) -> str:
    """The file for a definition: what `fileformat.parse` reads back to the same fields."""
    return fileformat.render(from_fields(rid, fields, provenance, spec), spec)


def logged(fields: dict[str, Any], cfg: Config) -> dict[str, Any]:
    """``fields`` as the committed log holds them (the ``log`` redaction profile), so a
    digest compared with a recorded one is of the same thing."""
    return R.redact_leaves(dict(fields), redactor("log", cfg))  # type: ignore[return-value]


def digest_of(fields: dict[str, Any], cfg: Config) -> str:
    """The ``digest`` a ``def.*`` event carries for these fields."""
    return D.digest(logged(fields, cfg))
