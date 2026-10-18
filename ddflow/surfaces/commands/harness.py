"""`ddflow harness list | show | doctor`: the human surface for `api.harness`."""

from __future__ import annotations

import sys

from ...api.harness import harness_view
from ..context import FAIL, OK, Ctx


def cmd_harness(a, c: Ctx) -> int:
    out = harness_view(c.repo, what=a.harness_cmd, id=getattr(a, "id", "") or "")
    if out.exit == FAIL and not out.data.get("findings"):
        print(out.reason, file=sys.stderr)
        return FAIL
    c.out(out.data["message"], out.body(tuple(k for k in out.data if k != "message")))
    return out.exit if out.exit != OK else OK
