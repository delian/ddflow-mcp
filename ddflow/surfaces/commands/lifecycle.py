"""`wait` -- the one loop verb that stays a hand-written command: it streams progress to
stderr while it sleeps, which `Command.call` + `render` has no place for. The others
(`next`, `claim`, `complete`, `merge` and friends) run on the executor; how they read is in
`declared/lifecycle_cli.py`.
"""

from __future__ import annotations

import sys

from ...api import lifecycle as A
from ..context import OK, Ctx
from ..render import emit_json


def cmd_wait(a, c: Ctx) -> int:
    """Sleep until the item (or anything) can be claimed. Progress goes to stderr, so a
    harness watching the process -- a background shell, a monitor -- sees it move."""
    out = A.wait(
        c.repo,
        item=a.item or "",
        phase=a.phase or "",
        kind=a.kind,
        globs=a.globs,
        timeout_s=a.timeout,
        poll_s=a.poll,
        agent=c.requested_agent,
        on_progress=lambda msg: print(msg, file=sys.stderr, flush=True),
    )
    if c.json:
        emit_json(out.body())
        return out.exit
    if out.exit != OK:
        print(out.reason)
        return out.exit
    d = out.data
    print(f"READY: {', '.join(d['ready'])} (after {d['waited_s']}s)")
    for f in d["freed_by"]:
        print(f"  {f}")
    print(d["advice"])
    return OK
