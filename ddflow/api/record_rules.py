"""Enforcing a data kind's declared rules (`core.kindrules`) when a record is written.

``checked`` is called by add, edit and revise with what `api.records` has already type-checked:
it asks the log for the stored record (to judge a state change) and for linked records, and
answers the fields to store or why not. A kind with no rules, or whose records live
elsewhere, passes through untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import defs as D
from ..core import kindrules as KR
from ._base import _load


def checked(
    repo: Path, kind: Any, rid: str, stored: dict[str, Any], why: str, *, agent: str = ""
) -> tuple[dict[str, Any], str]:
    """``(fields, "")`` or ``({}, why)``; a ``why`` from the earlier check is passed on."""
    rules = getattr(kind, "rules", None)
    if why or rules is None or not kind.stored_kind:
        return stored, why
    st = _load(repo, agent)[2]
    held = st.defs.get(D.key(kind.def_kind, rid))
    fields = dict(stored)

    def exists(def_kind: str, other: str) -> bool:
        rec = st.defs.get(D.key(def_kind, other))
        return rec is not None and rec.live

    why = KR.problem(rules, fields, dict(held.fields) if held is not None else None, exists)
    return ({}, why) if why else (fields, "")
