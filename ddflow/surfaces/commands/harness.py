"""`ddflow harness list | show | doctor | facts | add | lint | test | probe | record`: the human surface for `api.harness`."""

from __future__ import annotations

import sys

from ...api.harness import harness_view
from ...api.harness_record import harness_record
from ...api.model_cmds import model_show
from ..context import FAIL, OK, Ctx


def cmd_harness(a, c: Ctx) -> int:
    out = harness_view(
        c.repo,
        what=a.harness_cmd,
        id=getattr(a, "id", "") or getattr(a, "agent", "") or "",
        unverified=bool(getattr(a, "unverified", False)),
        family=getattr(a, "family", "") or "",
        title=getattr(a, "title", "") or "",
        directory=getattr(a, "dir", "") or "",
        run=bool(getattr(a, "run", False)),
    )
    if out.exit == FAIL and not out.data.get("findings") and not out.data.get("message"):
        print(out.reason, file=sys.stderr)
        return FAIL
    c.out(out.data["message"], out.body(tuple(k for k in out.data if k != "message")))
    return out.exit if out.exit != OK else OK


def cmd_harness_record(a, c: Ctx) -> int:
    out = harness_record(
        c.repo,
        a.id,
        prepare=a.prepare,
        source=a.source,
        write=a.write,
        dest=a.dest,
        version=a.agent_version,
        agent=c.requested_agent,
        called_from=c.called_from,
    )
    if "message" not in out.data:
        print(out.reason, file=sys.stderr)
        return out.exit
    c.out(out.data["message"], out.body(tuple(k for k in out.data if k != "message")))
    return out.exit


def cmd_model(a, c: Ctx) -> int:
    out = model_show(c.repo, a.name)
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    c.out(out.data["message"], out.body(tuple(k for k in out.data if k != "message")))
    return OK
