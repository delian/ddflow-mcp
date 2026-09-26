"""`phase add`, `task add`, `split`, `update` — the human surface for `api.items`."""

from __future__ import annotations

import sys

from ...api import items as A
from ..context import FAIL, OK, Ctx


def cmd_phase_add(a, c: Ctx) -> int:
    out = A.phase_add(
        c.repo,
        a.id,
        title=a.title,
        needs=a.needs or "",
        globs=a.globs or "",
        body=a.body or "",
        tags=a.tags or "",
        priority=a.priority,
        agent=c.cfg.agent.id,
    )
    c.out(f"phase {a.id} added", out.body(("id",)))
    return out.exit


def cmd_task_add(a, c: Ctx) -> int:
    parent = a.parent or a.phase
    out = A.task_add(
        c.repo,
        a.id,
        title=a.title,
        parent=parent,
        needs=a.needs or "",
        globs=a.globs or "",
        body=a.body or "",
        tags=a.tags or "",
        priority=a.priority,
        agent=c.cfg.agent.id,
    )
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    released = ""
    if out.data["released_parent_lease"]:
        released = (
            f"\n  {parent} is now an umbrella, so its lease was released: the work is "
            f"in its sub-tasks, and holding it would block them."
        )
    c.out(f"task {a.id} added to {parent or '(no phase)'}{released}", out.body(("id",)))
    return OK


def cmd_split(a, c: Ctx) -> int:
    out = A.split(
        c.repo,
        a.id,
        into=list(a.into or []),
        globs=a.globs or "",
        needs=a.needs or "",
        agent=c.cfg.agent.id,
    )
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        return out.exit
    created = out.data["created"]
    tail = ""
    if out.data["inherited_globs"]:
        tail = (
            f"\n  Give each its own --globs with `ddflow update <id> --globs ...` if they "
            f"write different files — they inherited {a.id}'s, so they cannot run in "
            f"parallel until they differ."
        )
    c.out(
        f"{a.id} split into {len(created)} sub-task(s): {', '.join(created)}\n"
        f"  It keeps its id and history, and now completes when they do.{tail}",
        out.body(("item", "created")),
    )
    return OK
