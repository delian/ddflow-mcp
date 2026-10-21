"""`ddflow identify` -- who this shell is, and which layer said so (read-only).

The MCP `ddflow_identify` DECLARES an identity for a connection; a shell has no connection
to declare it on, so here the name comes from `--agent`, `DDFLOW_AGENT`, config, an
identity a harness declared over MCP, or the working tree. This reports the winner.
"""

from __future__ import annotations

from ..context import OK, Ctx
from ..registry import Command
from ..render import emit_json


def cmd_identify(a, c: Ctx) -> int:
    who = c.identity
    if c.json:
        emit_json({"agent": who.id, "source": who.source})
        return OK
    print(f"identified as {who.id!r} ({who.source})")
    if who.source in ("derived",):
        print(
            "Not declared: every agent in this working tree derives this same name. If you are "
            "one of several here, pass `--agent NAME` or set DDFLOW_AGENT."
        )
    return OK


COMMAND = Command(
    path=("identify",),
    summary="who this shell is and which layer named it (declaring is `--agent` / DDFLOW_AGENT)",
    handler=cmd_identify,
    via=("ddflow_identify",),
)
