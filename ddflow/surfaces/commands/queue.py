"""`phase add`, `task add`, `split`, `update`, `resolve` — the human surface for `api.items`."""

from __future__ import annotations

import shlex
import sys

from ...api import items as A
from ..context import OK, Ctx

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
        readd=a.readd,
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
        readd=a.readd,
        agent=c.requested_agent,
    )
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        return out.exit
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


def contest_block(it) -> str:
    """`show`'s CONTESTED block: each rival definition or claim, whole.

    Whole, not summarised: the point of recording a contest is that the losing side is
    not lost, and a title alone is not enough to re-file it from.
    """
    if not (it.contested or it.lease_contest):
        return ""
    lines = [f"CONTESTED — `ddflow resolve {it.id} --keep <event-id|agent>` settles it"]
    for d in it.contested:
        lines.append(f"  definition {d['event']} by {d['agent']} (lamport {d['lamport']})")
        lines.append(f"    title {d['title']!r}")
        lines += [f"    | {ln}" for ln in (d["body"] or "").splitlines()]
    for h in it.lease_contest:
        where = h["lease"].get("worktree") or "-"
        lines.append(f"  claim {h['event']} by {h['holder']} (worktree {where})")
    return "\n".join(lines)


def _resolved_text(d: dict) -> str:
    """What `resolve` kept, and -- for each definition it did not -- how to get it back."""
    lines = []
    if d["kept_definition"]:
        k = d["kept_definition"]
        lines.append(f"{d['id']}: kept definition {k['event']} by {k['agent']} ({k['title']!r})")
    if d["kept_holder"]:
        lines.append(f"{d['id']}: {d['kept_holder']} keeps the lease")
        lines += [f"  released {h}'s claim" for h in d["released"]]
    refiled = d["refiled"] or [""] * len(d["lost"])
    for lost, new in zip(d["lost"], refiled, strict=True):
        if new:
            lines.append(f"  re-filed {lost['agent']}'s definition {lost['title']!r} as {new}")
            continue
        body = f" --body {shlex.quote(lost['body'])}" if lost["body"] else ""
        lines.append(f"  NOT kept: {lost['event']} by {lost['agent']}")
        lines.append(f"    title {lost['title']!r}")
        lines += [f"    | {ln}" for ln in (lost["body"] or "").splitlines()]
        lines.append(
            f"    re-file it under a new id (or resolve with --refile-as <new-id>):\n"
            f"    ddflow {d['item_kind']} add <new-id> --title {shlex.quote(lost['title'])}{body}"
        )
    return "\n".join(lines)


def cmd_resolve(a, c: Ctx) -> int:
    out = A.resolve(c.repo, a.id, keep=a.keep, refile_as=a.refile_as or "", agent=c.requested_agent)
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        return out.exit
    c.out(_resolved_text(out.data), out.body(RESOLVE_PAYLOAD))
    return OK


#: `resolve`'s wire body, the same on `ddflow_resolve`.
RESOLVE_PAYLOAD = ("id", "kept_definition", "kept_holder", "lost", "refiled", "released")


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
