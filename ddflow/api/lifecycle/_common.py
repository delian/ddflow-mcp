"""What every lifecycle module shares: requiring an item to exist, and the completion
record of why no reviewer was needed (a chore's exemption, `services.chore`).

Part of `ddflow.api.lifecycle`, which re-exports the public names defined here."""

from __future__ import annotations

import time
from pathlib import Path

from ...core import outcome as O
from ...services import chore as CO
from ...services import completion as CM
from ...services.cleanup import stranded_text


def _require(st, item: str, kind: str, grace_s: int, mine: str | None = None):
    it = st.items.get(item)
    if it is None or it.removed:
        if it is not None and it.lease and it.lease.live(time.time(), grace_s):
            # A LIVE lease only (a lapsed one strands nothing): its holder's branch is still
            # out there (B71dedb76d2), so say so, not "no such item".
            ls = it.lease
            text = stranded_text(
                item, ls.holder, ls.branch or it.branch, ls.worktree, ls.clone, mine
            )
            return O.refused(kind, text, id=item)
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed(kind, f"no such item {item!r}{gone}", id=item)
    return it


def _chore_base(v) -> dict:
    """The completion OUTPUT's optional `chore` field: why no reviewer was needed (the
    verdict's exemption note, `services.chore`). In the base rather than the event only so
    BOTH surfaces are told, like the coverage gaps. Absent = not a chore."""
    return {"chore": v.chore} if v.chore else {}


def _chore_event(v, st, cfg, it, repo: Path) -> dict:
    """The `item.completed` EVENT's structured chore record: note + approver + FULL approval
    digest + paths, verifiable from the event alone (the note truncates the digest to 12
    chars); `note` is the same text its gate.skipped events carry. The verdict kept only
    the note, so the fields are re-derived from the same inputs it just judged (same
    moment) -- it cannot drift from `v.chore`. Absent = not a chore."""
    if not v.chore:
        return {}
    excused = CO.exemption(st, cfg, it, CM.changed_paths(repo, it))[0]
    return (
        {
            "chore": {
                "note": v.chore,
                "approved_by": excused.approved_by,
                "digest": excused.digest,
                "paths": list(excused.paths),
            }
        }
        if excused
        else {}
    )
