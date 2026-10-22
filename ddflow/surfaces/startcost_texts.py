"""The texts a session is handed before its first turn, for `ddflow doctor`'s start cost.

Both doctor surfaces (the command and the MCP tool) pass `start_texts` to the report, which
counts them (`services.startcost`). They live here because building them is the MCP server's
and the brief's business, and the api layer imports no surface."""

from __future__ import annotations

import json
from pathlib import Path

from ..services import startcost as SC


def start_texts(repo: Path, agent: str = "") -> dict[str, str]:
    """name -> text: the MCP instructions, `tools/list` for the active `[mcp].tools`
    profile (compact JSON, as it is sent) and the session brief the SessionStart hook prints.
    A part that cannot be built raises: `services.startcost.report` turns that into a said
    'could not be measured', never a quiet 0."""
    from ..api.lifecycle.brief import brief
    from . import mcp

    texts = {"instructions": "", "tools/list": "", "brief": ""}
    with SC.measuring():
        srv = mcp.Server(repo, agent)
        texts["instructions"] = mcp._instructions(repo, agent, srv.tier)
        listed = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = (listed or {}).get("result", {}).get("tools", [])
        texts["tools/list"] = json.dumps(tools, separators=(",", ":"))
        texts["brief"] = brief(repo, check_recovery=False, agent=agent).data.get("text", "")
    return texts
