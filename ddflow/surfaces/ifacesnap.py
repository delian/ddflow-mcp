"""Build and write the MCP interface snapshot from a live `Server` (SU-init-fast).

`build` collects what `mcpshim.snapshot` serves: it asks the server for its own `tools/list` and
`prompts/list` replies (so the snapshot cannot drift from them), takes the capabilities,
identity and protocol versions from `mcp_protocol`, and the digest from `ifacedigest`.
`refresh` writes only when something a client could see changed, so a restart that changes
nothing leaves the file (and the shim's page cache) alone.

`instructions` is the TEXT the shim will serve at `initialize`: the caller passes the stable
rendering. The volatile parts (crash leftovers, the upgrade notice) belong in the first tool
result footer, not here (SU-init-fast-hook).
"""

from __future__ import annotations

import time
from typing import Any

from ..api import mcp_snapshot
from ..core.version import running as running_version
from ..mcpshim import snapshot as S
from . import ifacedigest as D
from . import mcp_protocol as proto


def build(server: Any, *, instructions: str) -> dict[str, Any]:
    """The snapshot of `server`'s tier with `instructions` as its initialize text."""
    return {
        "form": S.FORM,
        "version": running_version(),
        "digest": D.digest(D.live_snapshot(server)),
        "tier": server.tier,
        "protocols": list(proto.SUPPORTED_PROTOCOLS),
        "modern_protocols": list(proto.MODERN_PROTOCOLS),
        "capabilities": proto.capabilities(),
        "server_info": dict(proto.SERVER_INFO),
        "instructions": instructions,
        "tools": server._m_tools_list({"id": 0}, False)["result"]["tools"],
        "prompts": server._m_prompts_list({"id": 0}, False)["result"]["prompts"],
    }


def drift(server: Any, data: dict[str, Any]) -> list[str]:
    """Where the replies the shim would serve from `data` differ from the live server's own.
    Empty when they agree (the instructions text is the caller's, so it is not compared)."""
    live_init = server._m_initialize({"id": 0, "params": {"protocolVersion": ""}}, False)["result"]
    mine = S.initialize_result(data, "")
    found = [
        k for k in ("protocolVersion", "capabilities", "serverInfo") if mine[k] != live_init[k]
    ]
    live_disc = server._discover()
    mine_disc = S.discover_result(data)
    found += [
        f"discover.{k}"
        for k in ("supportedVersions", "capabilities")
        if mine_disc[k] != live_disc[k]
    ]
    if S.tools_list_result(data) != server._m_tools_list({"id": 0}, False)["result"]:
        found.append("tools/list")
    if S.prompts_list_result(data) != server._m_prompts_list({"id": 0}, False)["result"]:
        found.append("prompts/list")
    return found


def write(server: Any, *, instructions: str) -> bool:
    """Write the snapshot unconditionally; True. Refuses (ValueError) a snapshot whose replies
    would not match the live server's: a shim must never serve what the worker would not."""
    data = build(server, instructions=instructions)
    if wrong := S.problems(data) or drift(server, data):
        raise ValueError(f"the interface snapshot would not reproduce: {', '.join(wrong)}")
    data["written_at"] = round(time.time(), 3)
    mcp_snapshot.write(server.repo, S.dumps(data))
    return True


def refresh(server: Any, *, instructions: str) -> bool:
    """Write only if the stored snapshot is missing, invalid, from another version, or differs in
    digest, tier or instructions. True when it wrote."""
    new = build(server, instructions=instructions)
    old = S.read(server.repo)
    if (
        old is not None
        and S.fresh(old, new["version"])
        and all(old[k] == new[k] for k in ("digest", "tier", "instructions"))
    ):
        return False
    return write(server, instructions=instructions)
