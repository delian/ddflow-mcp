"""Limits and lint for guidance, per kind.

Limits are the project's caps on how much guidance a kind may hold (``[rules]``:
``max_rules``, ``max_size_bytes``, ``scopes_allowed``, ``tags_allowed``); a kind with no
config section has none. Lint is what makes a record well-formed whatever its kind: the same
checks run when a file is read and when a record is built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...core import clock
from .record import ENFORCEMENTS, STATUSES, GuidanceRecord, KindSpec


@dataclass(frozen=True)
class Limits:
    section: str  # the config section that holds them: the name in the refusal
    max_items: int
    max_size_bytes: int
    scopes_allowed: list[str]
    tags_allowed: list[str]


def limits_for(cfg: Any, spec: KindSpec) -> Limits | None:
    """The limits ``cfg`` sets for a kind, or None when the kind has no config section."""
    sec = getattr(cfg, spec.section, None) if spec.section else None
    if sec is None:
        return None
    return Limits(
        spec.section,
        getattr(sec, f"max_{spec.directory}"),
        sec.max_size_bytes,
        sec.scopes_allowed,
        sec.tags_allowed,
    )


def over_limit(rec: GuidanceRecord, lim: Limits | None, existing: int) -> str:
    """Why a NEW record breaks the limits (``existing`` already filed), or ""."""
    if lim is None:
        return ""
    s, directory = lim.section, rec.kind + "s"
    if len(rec.body.encode("utf-8")) > lim.max_size_bytes:
        return f"content is over {s}.max_size_bytes ({lim.max_size_bytes})"
    if rec.level not in lim.scopes_allowed:
        return f"scope {rec.level!r} is not in {s}.scopes_allowed {lim.scopes_allowed}"
    if lim.tags_allowed and (bad := [t for t in rec.tags if t not in lim.tags_allowed]):
        return f"tags {bad} are not in {s}.tags_allowed {lim.tags_allowed}"
    if existing >= lim.max_items:
        return f"the project already has {s}.max_{directory} ({lim.max_items}) {directory}"
    return ""


def lint(rec: GuidanceRecord, spec: KindSpec) -> list[str]:
    """Every way ``rec`` is not well-formed for its kind, one sentence each; [] when it is."""
    out: list[str] = []
    if not spec.valid_id(rec.id):
        out.append(f"Invalid {spec.noun} id '{rec.id}': {spec.id_hint}")
    if rec.status not in STATUSES:
        out.append(f"unknown status {rec.status!r}: one of {', '.join(STATUSES)}")
    if rec.enforcement not in ENFORCEMENTS:
        out.append(f"unknown enforcement {rec.enforcement!r}: one of {', '.join(ENFORCEMENTS)}")
    if rec.review_by:
        try:
            clock.parse_date(rec.review_by)
        except ValueError:
            out.append(f"review_by {rec.review_by!r} is not a date (YYYY-MM-DD)")
    return out
