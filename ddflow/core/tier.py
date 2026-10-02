"""The model-tier hint: a `tier:fast|balanced|deep` tag on a task. Advisory only.

A harness that dispatches a subagent for a task may read it to pick a model -- cheap for
mechanical bulk work, balanced for implementation, top-tier for architecture trade-offs.
It never reaches reviewer independence, gates or scheduling; ddflow only shows it
(`brief`, `next`) and notes a value it does not know (`doctor`).
"""

from __future__ import annotations

from collections.abc import Iterable

from .model import ABANDONED, DONE

TIERS = ("fast", "balanced", "deep")
PREFIX = "tier:"


def tier_of(tags: Iterable[str] | None) -> str:
    """The first valid tier among ``tags``, or "" when there is none."""
    for t in tags or ():
        v = t.strip().lower()
        if v.startswith(PREFIX) and v[len(PREFIX) :].strip() in TIERS:
            return v[len(PREFIX) :].strip()
    return ""


def unknown_tiers(tags: Iterable[str] | None) -> list[str]:
    """The `tier:` tags whose value is not one of `TIERS`."""
    return [
        t
        for t in tags or ()
        if t.strip().lower().startswith(PREFIX)
        and t.strip().lower()[len(PREFIX) :].strip() not in TIERS
    ]


def unknown_tier_notes(items: Iterable) -> list[str]:
    """One doctor NOTE per live item carrying a `tier:` tag with an unknown value."""
    notes = []
    for it in items:
        if it.removed or it.state in (DONE, ABANDONED):
            continue
        if bad := unknown_tiers(it.tags):
            notes.append(
                f"{it.id}: unknown tier tag {', '.join(bad)} — the advisory model-tier hint "
                f"is one of {', '.join(PREFIX + t for t in TIERS)}; it is ignored"
            )
    return notes
