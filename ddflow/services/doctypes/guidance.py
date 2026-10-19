"""What a document's type tells the agent writing it (B-dt-rules).

A type may carry a ``[doctype.guidance]`` table -- ``audience``, ``tone``, ``style`` (a style
guide), free ``text`` and a ``rules`` list -- and its sections their own ``prompt`` and
``guidance``. Each becomes guidance the one injection path already hands out (`guidance.inject`):
a record scoped to the type's documents, so it reaches the claim, the brief and the review
prompts of any item whose files overlap one, and no other. The type's ``rules`` are one record
each, so a reviewer can cite one. The records are read from the type files on demand and never
stored: the type file is the source, and `provenance` says which one.
"""

from __future__ import annotations

from pathlib import Path

from ..guidance.kinds import RULE
from ..guidance.record import GuidanceRecord, Scope
from . import spec
from .find import type_glob, types_of

#: Shipped types are the product's; a project-added one is a repository's own words.
SHIPPED = "shipped/"


def _body(dt: spec.DocType) -> str:
    g = dt.guidance
    rows = [f"{dt.title} documents ({dt.genre}, {dt.contract})."]
    rows += [f"{k.capitalize()}: {g[k]}" for k in ("audience", "tone", "style") if g.get(k)]
    if g.get("text"):
        rows.append(str(g["text"]))
    for sec in dt.sections:
        hint = " ".join(x for x in (sec.prompt, sec.guidance) if x)
        if hint:
            rows.append(f"- {sec.heading or sec.id}: {hint}")
    return "\n".join(rows)


def _has_content(dt: spec.DocType) -> bool:
    g = dt.guidance
    return any(g.get(k) for k in ("audience", "tone", "style", "text")) or any(
        s.prompt or s.guidance for s in dt.sections
    )


def records_of(dt: spec.DocType) -> list[GuidanceRecord]:
    """The guidance records of one type: its writing guidance, then one per ``rules`` entry."""
    scope = Scope(globs=(type_glob(dt),))
    who = "operator" if dt.source.startswith(SHIPPED) else "agent"
    common = {
        "kind": RULE.kind,
        "scope": scope,
        "tags": ["doctype"],
        "sources": [dt.source],
        "provenance": {"by": f"document type {dt.name}"},
        "ext": {"decided_by": who},
        "level": RULE.default_level,
    }
    out = []
    if _has_content(dt):
        out.append(
            GuidanceRecord(f"dt-{dt.name}", title=f"{dt.title} documents", body=_body(dt), **common)
        )
    for i, text in enumerate(dt.guidance.get("rules", []), 1):
        out.append(
            GuidanceRecord(
                f"dt-{dt.name}-r{i}",
                title=f"{dt.title}: rule {i}",
                body=text,
                priority=60,
                **common,
            )
        )
    return out


def records(root: Path | str | None) -> list[GuidanceRecord]:
    """The guidance of every type of the project at ``root`` (none when it has no root, or its
    type files cannot be read: `ddflow docs check` says so, a claim must not fail on it)."""
    if root is None:
        return []
    try:
        types = types_of(root)
    except (spec.DocTypeError, OSError):
        return []
    return [r for dt in types for r in records_of(dt)]
