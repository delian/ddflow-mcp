"""The sensitivity label of a record, set when it is written (SE-sensitivity.2).

`checked` runs in the same seam as `record_rules.checked`. A record may carry
``sensitivity`` (any kind); it must name a level of the vocabulary (`services.sensitivity`).
With ``[sensitivity].scan_at_write`` the text written is scanned by `core.redact` (the session
patterns, so a project's own secret shapes count) and the label is raised to the highest of
the declared label, the label already held, and what the scan found. Never lowered by the scan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import defs as D
from ..core.redact import redact_text
from ..services.redact_report import session_patterns
from ..services.sensitivity import load
from ._base import _load

FIELD = "sensitivity"


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
    if not cfg.sensitivity.scan_at_write:
        return stored, ""
    held = st.defs.get(D.key(kind.def_kind, rid))
    prior = (held.fields.get(FIELD) if held is not None else None) or None
    found = redact_text(_text(stored), secret_patterns=session_patterns(cfg)).counts
    level = vocab.scan_level(found, floor=declared or prior or "")
    if declared is None and level == vocab.unlabelled and prior is None:
        return stored, ""  # nothing to say: an unlabelled record is at the default
    return {**stored, FIELD: level}, ""
