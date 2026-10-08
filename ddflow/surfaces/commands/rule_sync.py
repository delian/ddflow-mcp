"""`ddflow rule sync` -- make the log and the rule files agree (D-unify 7).

Same call and the same wire body as the `ddflow_rule_sync` MCP tool."""

from __future__ import annotations

import sys

from ...api import rule_sync
from ...core.outcome import FAIL
from ..context import Ctx
from ..render import emit_json

PAYLOAD = ("recorded", "updated", "restored", "failed")
_SAID = (
    ("recorded", "recorded"),
    ("updated", "hand edit(s) recorded"),
    ("restored", "file(s) written from the log"),
)


def _line(data: dict) -> str:
    said = [f"{len(data[k])} {what}: {', '.join(data[k])}" for k, what in _SAID if data.get(k)]
    return "; ".join(said) or "rule files and the log agree"


def cmd_rule_sync(a, c: Ctx) -> int:
    out = rule_sync(c.repo, agent=c.log.agent_id if getattr(c, "log", None) else "")
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        emit_json(out.body(PAYLOAD))
    else:
        print(_line(out.data))
    return out.exit
