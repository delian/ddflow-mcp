"""E3 mcp_shape: where an MCP server entry lives in an agent's file, and what it looks like.

Container paths and entry templates are mcpconfig's (one definition). This is the data-shaped
face: a container path, the entry, and the JSON serialiser. TOML (Codex) and the Cordis YAML
overlay stay with ``mcpconfig.register`` -- they are merges into the operator's file, a writer.
"""

from __future__ import annotations

import json

from .. import mcpconfig
from .safety import EngineError

#: shape -> path of the server table inside the JSON document
CONTAINERS: dict[str, tuple[str, ...]] = {
    mcpconfig.SHAPE_MCP_SERVERS: ("mcpServers",),
    mcpconfig.SHAPE_COPILOT: ("mcpServers",),
    mcpconfig.SHAPE_SERVERS: ("servers",),
    mcpconfig.SHAPE_MCP_DOT_SERVERS: ("mcp", "servers"),
    mcpconfig.SHAPE_OPENCODE: ("mcp",),
}
#: serialiser per shape; the two others are named so a caller can route to the writer
SERIALISERS: dict[str, str] = {
    **dict.fromkeys(CONTAINERS, "json"),
    mcpconfig.SHAPE_TOML: "toml",
    mcpconfig.SHAPE_CORDIS_PATCH: "yaml",
}


def _no_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def container_path(shape: str) -> tuple[str, ...]:
    try:
        return CONTAINERS[shape]
    except KeyError:
        raise EngineError(
            f"no JSON container for MCP shape {shape!r}; known: {sorted(CONTAINERS)}"
        ) from None


def entry(shape: str, launch: dict) -> dict:
    """``launch`` (command, args, env) rewritten the way this shape stores it."""
    container_path(shape)
    return mcpconfig.server_entry_for(shape, launch)


def render_json(shape: str, name: str, launch: dict, existing: str = "") -> str:
    """``existing`` (the operator's file text, or empty) with server ``name`` placed; siblings kept."""
    container_path(shape)
    try:
        data = json.loads(existing, parse_constant=_no_constant) if existing.strip() else {}
    except (ValueError, RecursionError) as exc:
        raise EngineError(f"the existing file is not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise EngineError("the existing file is JSON but not an object")
    try:
        mcpconfig.place_server(data, shape, name, launch)
    except mcpconfig.UnplaceableConfig as exc:
        raise EngineError(str(exc)) from None
    try:
        return json.dumps(data, indent=2, allow_nan=False) + "\n"
    except ValueError as exc:  # a number such as 1e999 loads as infinity
        raise EngineError(f"the existing file holds a number JSON cannot store: {exc}") from None
