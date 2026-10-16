"""Applying an extension answer (decision D-no-duplicates): the text, and now the globs, go
onto the existing record. Re-exported as ``_dedupe.extend``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import Config
from ..core import outcome as O

if TYPE_CHECKING:
    from ._dedupe import Checked


def _table(st, kind: str):
    """The state table holding records of a kind that carries globs; None for the rest."""
    return {
        "decision": st.decisions,
        "lesson": st.lessons,
        "task": st.items,
        "phase": st.items,
    }.get(kind)


def _new_globs(st, target: str, kind: str, globs: list[str]) -> list[str]:
    """The ``globs`` the extended record does not carry yet, in the order given. A lesson
    takes them only when it already declares a pattern: its globs scope that inventory."""
    table = _table(st, kind)
    rec = table.get(target) if table is not None else None
    if rec is None or (kind == "lesson" and not rec.pattern):
        return []
    have = set(rec.globs or [])
    return [g for g in dict.fromkeys(globs) if g not in have]


def extend(
    log, cfg: Config, chk: Checked, event_kind: str, *, st=None, globs: list[str] | None = None
) -> O.Outcome:
    """Append the text to the record it was answered onto. No new id is made; the
    outcome names the record that received it. The original text is never touched.

    ``globs`` given with the add are not text: they are UNIONED into the extended
    decision / task / phase / lesson that carries globs (``globs_added`` names what was
    new)."""
    ex = chk.extension
    assert ex is not None
    added = _new_globs(st, ex["target"], ex["kind"], globs) if globs and st is not None else []
    log.append(
        "record.extended",
        ex["target"],
        {
            "text": ex["text"],
            "who": cfg.agent.id,
            "score": ex["score"],
            "relation": ex["relation"],
            "auto": ex["auto"],
            "dedupe": ex["dedupe"],
            **({"globs": added} if added else {}),
        },
    )
    return O.ok(
        event_kind,
        id=ex["target"],
        extended=ex["target"],
        extended_kind=ex["kind"],
        relation=ex["relation"],
        score=ex["score"],
        auto=ex["auto"],
        candidates=chk.shown,
        globs_added=added,
    )
