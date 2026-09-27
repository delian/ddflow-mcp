"""`phase add`, `task add`, `split`, `update` — the human surface for `api.items`."""

from __future__ import annotations

import sys

from ...api import items as A
from ..context import FAIL, OK, Ctx

#: `task add`'s wire body, the same on `ddflow_task_add`: the ports it generated are
#: part of the result, not a detail of the human message.
TASK_ADD_PAYLOAD = ("id", "line", "ports", "port_strategy", "defaulted")


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
        line=a.line or "",
        agent=c.requested_agent,
    )
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        return out.exit
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
        line=a.line or "",
        lines=a.lines or "",
        agent=c.requested_agent,
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
    ports = ""
    if out.data["ports"]:
        chosen = (
            f" ({', '.join(out.data['defaulted'])} was not chosen: the default is now "
            f"recorded and followed — `ddflow flow show`)"
            if out.data["defaulted"]
            else ""
        )
        ports = (
            f"\n  written on line {out.data['line']}; {out.data['port_strategy']} ports: "
            f"{', '.join(out.data['ports'])}{chosen}"
        )
        if out.data["port_note"]:
            ports += f"\n  {out.data['port_note']}"
    c.out(
        f"task {a.id} added to {parent or '(no phase)'}{released}{ports}",
        out.body(TASK_ADD_PAYLOAD),
    )
    return OK


def cmd_split(a, c: Ctx) -> int:
    out = A.split(
        c.repo,
        a.id,
        into=list(a.into or []),
        globs=a.globs or "",
        needs=a.needs or "",
        agent=c.requested_agent,
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
