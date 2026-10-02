"""List viewers over the folded state (read-only): one Outcome per request."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..services import viewers as V
from ._base import _load


def view_list(
    repo: Path,
    kind: str,
    *,
    state: str = "",
    phase: str = "",
    tag: str = "",
    agent: str = "",
    since: str = "",
    limit: int = V.DEFAULT_LIMIT,
) -> O.Outcome:
    """Filtered, bounded rows of `kind` (task|phase|bug|research|session)."""
    _log, cfg, st = _load(repo)
    try:
        view = V.list_view(
            st, cfg, kind, state=state, phase=phase, tag=tag, agent=agent, since=since, limit=limit
        )
    except V.ViewError as exc:
        return O.refused("view.list", str(exc), record_kind=kind)
    data: dict[str, Any] = {
        "record_kind": view.kind,
        "rows": view.rows,
        "total": view.total,
        "shown": len(view.rows),
        "limit": view.limit,
        "truncated": view.truncated,
        "filters": view.filters,
    }
    if not view.rows:
        which = f" matching {view.filters}" if view.filters else ""
        return O.nothing("view.list", f"No {kind} records{which}.", **data)
    return O.ok("view.list", **data)
