"""`phase add` and `task add` — the human surface for `api.items`. (`split` and `resolve` run on
the executor: `declared/queue_cli.py`.)"""

from __future__ import annotations

from ...api import items as A
from .. import dedupe_flags as D
from ..context import OK, Ctx

#: `task add`'s wire body, the same on `ddflow_task_add`: the ports it generated are
#: part of the result, not a detail of the human message.
TASK_ADD_PAYLOAD = ("id", "line", "ports", "port_strategy", "defaulted")


def cmd_phase_add(a, c: Ctx) -> int:
    out = D.run(
        a,
        c,
        lambda answer: A.phase_add(
            c.repo,
            a.id,
            title=a.title,
            needs=a.needs or "",
            globs=a.globs or "",
            body=a.body or "",
            tags=a.tags or "",
            priority=a.priority,
            line=a.line or "",
            readd=a.readd,
            answer=answer,
            agent=c.requested_agent,
        ),
    )
    done = D.finish(a, c, out)
    if done is not None:
        return done
    c.out(f"phase {a.id} added", out.body(("id",)))
    return out.exit


def cmd_task_add(a, c: Ctx) -> int:
    parent = a.parent or a.phase
    out = D.run(
        a,
        c,
        lambda answer: A.task_add(
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
            port_of=a.port_of or "",
            readd=a.readd,
            answer=answer,
            agent=c.requested_agent,
        ),
    )
    done = D.finish(a, c, out)
    if done is not None:
        return done
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
