"""`ddflow metrics`: log-derived metrics, read-only.

Nothing here writes an event: every view is a projection of the log as read now, with the
time injected (``now``) so a test freezes it.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..core import outcome as O
from ..services import metrics as M
from ._base import _load


def run_metrics(
    repo: Path,
    *,
    view: str = "summary",
    item: str = "",
    phase: str = "",
    since: str = "",
    group_by: str = "",
    now: float | None = None,
    agent: str = "",
) -> O.Outcome:
    """One of the metrics views (summary, forecast, retro, skips, lanes). Exit 2 when the
    log holds too little to compute a figure: never a zero standing in for one."""
    log, cfg, st = _load(repo, agent)
    at = time.time() if now is None else now
    ans = M.run(
        log.read_all(),
        st,
        cfg,
        at,
        view=view or "summary",
        item=item or "",
        phase=phase or "",
        since=since or "",
        group_by=group_by or "",
    )
    data = {
        "view": view or "summary",
        "now": at,
        "phase": phase or "",
        "since": since or "",
        "metrics": ans.metrics,
        "text": ans.text,
    }
    if ans.failed:
        return O.failed("metrics", ans.reason, **data)
    if not ans.ok:
        return O.nothing("metrics", ans.reason, **data)
    return O.ok("metrics", **data)
