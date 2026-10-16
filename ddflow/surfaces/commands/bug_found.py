"""`ddflow bug found`: file a bug, with its reproduction bundle (DX-bug-repro-bundle)."""

from __future__ import annotations

from ...api import knowledge as A
from .. import dedupe_flags as D
from ..context import OK, Ctx


def _bug_found(a, c: Ctx) -> int:
    out = D.run(
        a,
        c,
        lambda answer: A.bug_found(
            c.repo,
            summary=a.summary,
            item=a.item or "",
            id=a.id or "",
            title=a.title,
            severity=a.severity,
            scope=a.scope,
            globs=a.globs,
            no_task=a.no_task,
            test=a.test or "",
            repro_cmd=a.repro_cmd or "",
            repro_capture=a.repro_capture or "",
            answer=answer,
            agent=c.requested_agent,
        ),
    )
    done = D.finish(a, c, out)
    if done is not None:
        return done
    closed = out.data.get("resolution", "")
    note = f" -- already closed as {closed}; this report does not reopen it" if closed else ""
    offer = out.data.get("offer", "")
    fix = out.data.get("fix_task", "")
    task = ""
    if fix and out.data.get("fix_task_filed"):
        task = f"\nfix task {fix} filed in the queue (claim it to fix; `complete {fix} --regression-test <test>` closes the bug)"
    elif fix:
        task = f"\nfix task: {fix}"
    link = out.data.get("repro") or {}
    if link.get("ref"):
        task += f"\nrepro bundle stored ({link['mode']}, {link['bytes']} bytes)"
    elif link.get("unavailable"):
        task += f"\nrepro bundle NOT stored: {link['unavailable']}"
    c.out(
        f"bug {out.data['id']} recorded{note}{task}" + (f"\n{offer}" if offer else ""),
        out.body(("id", "offer", "fix_task", "fix_task_filed")),
    )
    return OK
