"""The priority a task gets when its filer gave none (DX-default-priority-for-dx).

2026-10-16: 26 DX tasks and 6 fix tasks sat at the generic 100, behind every feature task
(median 52), because agents filed them without `--priority`. The operator's rule is that fixes
and immediate corrections are prioritized, so a task whose kind says so is filed into a band:

* a DX task -- tagged one of `[schedule].dx_tags`, or with an id starting `DX-` -- at
  `[schedule].dx_priority`;
* a bug-fix task -- tagged one of `[flow].bugfix_tags` / `hotfix_tags` -- at
  `[schedule].bug_priority`.

An explicit priority is never changed, `--priority 100` included.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..core.defaults import DEFAULT_PRIORITY

if TYPE_CHECKING:
    from ..config import Config

DX_ID_PREFIX = "DX-"


def kind_of(cfg: Config, item_id: str, tags: Iterable[str]) -> str:
    """ "dx", "bug" or "" for a task's id and tags."""
    have = set(tags)
    if item_id.startswith(DX_ID_PREFIX) or have & set(cfg.schedule.dx_tags):
        return "dx"
    if have & {*cfg.flow.bugfix_tags, *cfg.flow.hotfix_tags}:
        return "bug"
    return ""


def band(cfg: Config, item_id: str, tags: Iterable[str]) -> int:
    """The priority of a task filed with none: its band, else the generic default."""
    kind = kind_of(cfg, item_id, tags)
    if kind == "dx":
        return cfg.schedule.dx_priority
    if kind == "bug":
        return cfg.schedule.bug_priority
    return DEFAULT_PRIORITY


def left_generic(cfg: Config, item_id: str, tags: Iterable[str], priority: int) -> str:
    """A sentence when a DX or fix task carries the generic default ``priority``; else ""."""
    kind = kind_of(cfg, item_id, tags)
    if not kind or priority != DEFAULT_PRIORITY:
        return ""
    want = cfg.schedule.dx_priority if kind == "dx" else cfg.schedule.bug_priority
    label = "a DX task" if kind == "dx" else "a bug-fix task"
    return (
        f"{item_id} is {label} at the generic priority {DEFAULT_PRIORITY}, behind every feature: "
        f"omit priority to file it at {want}, or set it"
    )
