"""`claim <bug id>`: the fix task of an open bug, filed first when it has none (Bee5f08be7b)."""

from __future__ import annotations

from pathlib import Path

from ...core import outcome as O
from .._base import _load
from .bugs import bug_file_tasks


def claim_target(repo: Path, item: str, agent: str = "") -> tuple[str, str] | O.Outcome:
    """``(item to claim, bug id)``: ``item`` and "" unless it names no queue item but an OPEN
    bug, whose fix task is then the item. An id in the queue always means the item; an
    unknown or closed bug is left for `claim` to refuse as any unknown id."""
    _log, _cfg, st = _load(repo, agent)
    bug = st.bugs.get(item)
    if item in st.items or bug is None or not bug.open:
        return item, ""
    made = bug_file_tasks(repo, bug_id=item, agent=agent)
    if made.exit not in (O.OK, O.NOTHING):
        return made
    _log, _cfg, st = _load(repo, agent)
    task = st.bugs[item].fix_task
    if not task or task not in st.items:
        return O.failed("item.claimed", f"bug {item} has no fix task to claim", id=item)
    return task, item
