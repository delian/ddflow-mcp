"""The texts a session is handed before its first turn, for `ddflow doctor`'s start cost.

Both doctor surfaces (the command and the MCP tool) pass `start_texts` to the report, which
counts them (`services.startcost`). They live here because building them is the MCP server's
and the brief's business, and the api layer imports no surface."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from ..core.measuring import measuring


def start_texts(repo: Path, agent: str = "") -> dict[str, str]:
    """name -> text: the MCP instructions, `tools/list` for the active `[mcp].tools`
    profile (compact JSON, as it is sent) and the session brief the SessionStart hook prints.
    A part that cannot be built raises: `services.startcost.report` turns that into a said
    'could not be measured', never a quiet 0."""
    # By name, not `import`: the MCP server imports the tool declarations that import this
    # module (a cycle), and the tool table must load without the api.
    mcp = importlib.import_module("ddflow.surfaces.mcp")
    brief = importlib.import_module("ddflow.api.lifecycle.brief").brief  # the api stays unloaded
    texts = {"instructions": "", "tools/list": "", "brief": ""}
    with measuring():
        srv = mcp.Server(repo, agent)
        texts["instructions"] = mcp._instructions(repo, agent, srv.tier)
        listed = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = (listed or {}).get("result", {}).get("tools", [])
        texts["tools/list"] = json.dumps(tools, separators=(",", ":"))
        texts["brief"] = brief(repo, check_recovery=False, agent=agent).data.get("text", "")
    return texts


def schema_tokens(names, *, bytes_per_token: float = 4.0) -> int:
    """Tokens (bytes over a ratio, as the meter counts them) of the compact `tools/list` entries
    for ``names``: what a client that loads those schemas up front pays."""
    mcp = importlib.import_module("ddflow.surfaces.mcp")
    entries = [
        {
            "name": n,
            "description": mcp.TOOLS[n]["description"],
            "inputSchema": mcp._schema(mcp.TOOLS[n]),
        }
        for n in sorted(set(names))
        if n in mcp.TOOLS
    ]
    return round(len(json.dumps(entries, separators=(",", ":")).encode("utf-8")) / bytes_per_token)


def role_tokens(role: str, *, bytes_per_token: float = 4.0) -> int:
    """The ddflow schema tokens a lean agent definition (`services.leanagents`) admits for ``role``."""
    LA = importlib.import_module("ddflow.services.leanagents")
    return schema_tokens(LA.ddflow_tools(role), bytes_per_token=bytes_per_token)
