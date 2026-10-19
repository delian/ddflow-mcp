"""Validate rules about the front matter: a schema, drafts, and review dates."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

from ...core import clock
from ..docscheck_types import Finding
from .validate import Ctx, rule

_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "array": list,
    "number": (int, float),
    "object": dict,
    "null": type(None),
}
DRAFT_STATES = ("draft", "proposed", "wip")


def _type_ok(value: Any, name: str) -> bool:
    if name in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[name])


def schema_problems(front: Mapping[str, Any], schema: Mapping[str, Any]) -> Iterator[str]:
    """What a JSON-Schema subset (``required``, ``properties`` with ``type`` ``enum``
    ``pattern``) finds wrong with ``front``."""
    for key in schema.get("required", []):
        if key not in front:
            yield f"{key!r} is required"
    for key, spec in schema.get("properties", {}).items():
        if key not in front:
            continue
        v = front[key]
        if "type" in spec and spec["type"] not in _TYPES:
            yield f"{key!r}: the schema type {spec['type']!r} is not supported ({', '.join(_TYPES)})"
        elif "type" in spec and not _type_ok(v, spec["type"]):
            yield f"{key!r} must be {spec['type']}"
        elif "enum" in spec and v not in spec["enum"]:
            yield f"{key!r} is {v!r}; one of {', '.join(map(str, spec['enum']))}"
        elif "pattern" in spec and not re.search(spec["pattern"], str(v)):
            yield f"{key!r} does not match /{spec['pattern']}/"


@rule("front_matter", "schema")
def front_matter(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    schema = r.get("schema")
    if not isinstance(schema, dict):
        yield ctx.finding("front_matter", 1, "schema must be a table", r)
        return
    try:
        for problem in schema_problems(ctx.doc.front, schema):
            yield ctx.finding("front_matter", 1, problem, r)
    except (re.error, TypeError, AttributeError) as exc:
        yield ctx.finding("front_matter", 1, f"the schema cannot be applied: {exc}", r)


@rule("no_draft")
def no_draft(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    """A document the type calls accepted is not still a draft."""
    f = ctx.doc.front
    if f.get("draft") is True or str(f.get("status", "")).strip().casefold() in DRAFT_STATES:
        yield ctx.finding("no_draft", 1, "the front matter marks this document a draft", r)


@rule("not_expired")
def not_expired(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    """``review_by`` (an ISO date) is not in the past."""
    raw = ctx.doc.front.get("review_by")
    if raw is None:
        return
    try:
        due = clock.parse_date(str(raw))
    except ValueError:
        yield ctx.finding("not_expired", 1, f"review_by {raw!r} is not an ISO date", r)
        return
    if due < ctx.today:
        yield ctx.finding("not_expired", 1, f"review_by {due} has passed", r)
