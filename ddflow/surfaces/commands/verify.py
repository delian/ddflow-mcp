"""`ddflow verify <id>` -- the human surface for `api.verify`.

Same call, same wire body as the `ddflow_verify` MCP tool; exit 1 when a claim does not
hold, 2 when the item is not done (nothing to verify), 0 otherwise.
"""

from __future__ import annotations

import json
import sys

from ...api.verify import verify
from ..context import FAIL, NOTHING, Ctx

_MARK = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "unknown": "??  "}


def cmd_verify(a, c: Ctx) -> int:
    out = verify(c.repo, a.id)
    if out.exit == FAIL and not out.data.get("claims"):
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        print(json.dumps(out.body(("item", "verdict", "claims")), indent=2, default=str))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason, file=sys.stderr)
        return NOTHING
    print(f"{out.data['item']}: {out.data['verdict']}")
    for cl in out.data["claims"]:
        print(f"  [{_MARK[cl['status']]}] {cl['id']:<15} {cl['detail']}")
    return out.exit
