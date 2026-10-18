"""The sensitivity label of a record, set when it is written (SE-sensitivity.2).

`checked` runs in the same seam as `record_rules.checked`. A record may carry
``sensitivity`` (any kind); it must name a level of the vocabulary (`services.sensitivity`).
With ``[sensitivity].scan_at_write`` the text written is scanned by `core.redact` (the session
patterns, so a project's own secret shapes count) and the label is the highest of the label
given and what the scan found. With no label given, the label already held is the floor, so a scan
never lowers one; a write that GIVES a lower label lowers it on purpose (declassifying under an
approval is SE-approvals' to add), though never below what the scan finds in the text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import defs as D
from ..core.sensitivity import LABEL_FIELD
from ..services.redact_report import redactor
from ..services.sensitivity import load
from ._base import _load

FIELD = LABEL_FIELD


def _text(fields: dict[str, Any]) -> str:
    parts = [v for k, v in fields.items() if k != FIELD]
    return "\n".join(
        " ".join(map(str, p)) if isinstance(p, list | tuple) else str(p) for p in parts if p
    )


def checked(
    repo: Path, kind: Any, rid: str, stored: dict[str, Any], why: str, *, agent: str = ""
) -> tuple[dict[str, Any], str]:
    """``(fields with the label, "")`` or ``({}, why)``."""
    if why or not kind.stored_kind:
        return stored, why
    vocab = load(repo)
    declared = stored.get(FIELD)
    if declared is not None and declared not in vocab.levels:
        return (
            {},
            f"sensitivity {declared!r} is not a level; the levels are {', '.join(vocab.levels)}",
        )
    _log, cfg, st = _load(repo, agent)
    held = st.defs.get(D.key(kind.def_kind, rid))
    prior = (held.fields.get(FIELD) if held is not None else None) or None
    stale = prior is not None and prior not in vocab.levels
    if stale:
        # a label the vocabulary no longer has fails CLOSED: the highest level, until a write says otherwise
        prior = vocab.ordered()[-1].name
    if not cfg.sensitivity.scan_at_write:
        # a whole-record write (revise) that names no label keeps the one held
        return ({**stored, FIELD: prior} if prior and declared is None else stored), ""
    found = redactor("upstream", cfg).text(_text(stored)).counts
    level = vocab.scan_level(found, floor=declared or prior or "")
    if declared is None and level == vocab.unlabelled and prior is None and not stale:
        return stored, ""  # nothing to say: an unlabelled record is at the default
    return {**stored, FIELD: level}, ""
