"""The authored file: one format for ``.ddflow/rules/*.toml`` and ``.ddflow/decisions/*.toml``.

A file is TOML frontmatter, a blank line, and the text::

    id = "r-naming"
    title = "Naming conventions"
    tags = ["naming", "style"]
    scope = "project"
    priority = 75
    globs = ["**/*.py", "**/*.js"]
    created = "2026-10-03T12:00:00"
    updated = "2026-10-03T12:00:00"

    Follow snake_case for Python functions...

The frontmatter is the keys of `GuidanceRecord` (and the kind's own `extras`) under their own
names, written on single lines (a newline in a string is ``\\n``: the first blank line ends the
frontmatter). Keys written only when set: ``categories``, ``gates``, ``category``,
``enforcement`` (when not the kind's default), ``status`` (when not accepted), ``owner``,
``review_by``, ``sources``, ``checks``, ``links``. The text may instead be the ``content`` (or
``body``) key. A file written before these keys existed is read, and written back, unchanged.
"""

from __future__ import annotations

import tomllib
from datetime import datetime
from typing import Any

from ...core import clock
from ...infra.tomlcfg import value as _toml_value
from .limits import lint
from .record import ACCEPTED, GuidanceRecord, KindSpec, Scope

_FRONTMATTER_PARTS = 2  # frontmatter + content, split on the first blank line


def _strings(data: dict[str, Any], key: str, default: list[str] | None = None) -> list[str]:
    """``data[key]`` as a list of strings: a bare string is the one pattern it says (never
    its characters), a list is itself; anything else is a mistake worth naming."""
    got = data.get(key, default if default is not None else [])
    if isinstance(got, str):
        return [got]
    if isinstance(got, list) and all(isinstance(x, str) for x in got):
        return got
    raise ValueError(f"{key} must be a string or a list of strings, got {got!r}")


def _tables(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """``data[key]`` as a list of inline tables (a check is one), or a ValueError."""
    got = data.get(key, [])
    if isinstance(got, list) and all(isinstance(x, dict) for x in got):
        return got
    raise ValueError(f"{key} must be a list of tables, got {got!r}")


def parse(text: str, spec: KindSpec) -> GuidanceRecord:
    """The record in an authored file. Raises ValueError, naming the problem, for a file
    that is not TOML, lacks an id, a title or text, or breaks the kind's lint."""
    label = spec.label
    parts = text.split("\n\n", 1)
    has_block = len(parts) == _FRONTMATTER_PARTS
    frontmatter, block = parts if has_block else (text, "")
    try:
        data = tomllib.loads(frontmatter)
    except Exception as exc:
        raise ValueError(f"Invalid TOML in {spec.noun} frontmatter: {exc}") from exc

    for article, key in (("an", "id"), ("a", "title")):
        if key not in data:
            raise ValueError(f"{label} must have {article} '{key}' field")
    body_key = next((k for k in ("content", "body") if k in data), "")
    if not body_key and not has_block:
        raise ValueError(f"{label} must have a 'content' field or content block")

    now = clock.now_utc()
    rec = GuidanceRecord(
        id=data["id"],
        kind=spec.kind,
        title=data["title"],
        body=(data[body_key] if body_key else block).strip(),
        scope=Scope(
            globs=tuple(_strings(data, "globs")),
            categories=tuple(_strings(data, "categories")),
            gates=tuple(_strings(data, "gates")),
        ),
        level=data.get("scope", spec.default_level),
        category=data.get("category", ""),
        tags=_strings(data, "tags"),
        priority=int(data.get("priority", spec.default_priority)),
        enforcement=data.get("enforcement", spec.default_enforcement),
        status=data.get("status", ACCEPTED),
        owner=data.get("owner", ""),
        review_by=data.get("review_by", ""),
        sources=_strings(data, "sources"),
        checks=_tables(data, "checks"),
        links=_strings(data, "links"),
        ext={k: data.get(k, default) for k, default in spec.extras},
    )
    if spec.stamped:
        rec.provenance = {"created": data.get("created", now), "updated": data.get("updated", now)}
    if problems := lint(rec, spec):
        raise ValueError(problems[0])
    return rec


def _stamp(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def render(rec: GuidanceRecord, spec: KindSpec) -> str:
    """The authored file for ``rec``: what `parse` reads back."""
    lines = [f"id = {_toml_value(rec.id)}", f"title = {_toml_value(rec.title)}"]
    if rec.tags:
        lines.append(f"tags = {_toml_value(list(rec.tags))}")
    if rec.level or spec.default_level:
        lines.append(f"scope = {_toml_value(rec.level)}")
    lines.append(f"priority = {rec.priority}")
    if rec.scope.globs:
        lines.append(f"globs = {_toml_value(list(rec.scope.globs))}")
    if spec.stamped:
        for key in ("created", "updated"):
            lines.append(
                f"{key} = {_toml_value(_stamp(rec.provenance.get(key) or clock.now_utc()))}"
            )
    # Written only when set, so a file that never used them stays byte-for-byte what it was.
    optional: list[tuple[str, Any, Any]] = [
        ("categories", list(rec.scope.categories), []),
        ("gates", list(rec.scope.gates), []),
        ("category", rec.category, ""),
        ("enforcement", rec.enforcement, spec.default_enforcement),
        ("status", rec.status, ACCEPTED),
        ("owner", rec.owner, ""),
        ("review_by", rec.review_by, ""),
        ("sources", list(rec.sources), []),
        ("checks", list(rec.checks), []),
        ("links", list(rec.links), []),
        *((k, rec.ext.get(k, d), d) for k, d in spec.extras),
    ]
    lines += [f"{k} = {_toml_value(v)}" for k, v, default in optional if v != default]
    return "\n".join(lines) + f"\n\n{rec.body}"
