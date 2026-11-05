"""`_dispose`: the finished item's tree, branch and jobs, cleaned up by `complete`, `abandon` and `remove`.

Part of `ddflow.api.lifecycle.complete`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ....core.model import fold
from ....services import cleanup as CL
from ... import jobs as JB


def _dispose(repo: Path, cfg, log, item: str, callers) -> dict[str, Any]:
    """The item's own worktree and branch, removed when it is finished with and nothing is
    in them (B79319c6ada, B4757c428f4): ``worktree_removed`` and ``kept_reason`` ("" when
    there is nothing to say), the same two fields `merge` reports; and ``jobs_ended``, the item's
    jobs whose process is over, recorded as ended (absent when there were none), and ``jobs_note``
    when collecting them could not run."""
    it = fold(log.read_all(), strict=False).items.get(item)
    removed, why = (
        CL.dispose_finished(repo, cfg, log, it, callers=callers) if it is not None else (False, "")
    )
    ended, problem = JB.collect_for_item(log, item)  # its finished jobs end with it (B5127aa5436)
    return {
        "worktree_removed": removed,
        "kept_reason": why,
        **({"jobs_ended": ended} if ended else {}),
        **({"jobs_note": problem} if problem else {}),
    }
