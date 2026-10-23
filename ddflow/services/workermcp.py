"""The MCP config a lane worker starts with: ddflow on the `core` tier (LS-worker-mcp-config).

A client that loads every ddflow schema up front pays ~27K more than one on the `core` tier
(measured 84.2K against 54.7K first-turn input, docs/ddflow/research/lean-start.md lever 2),
and the tier is read from `DDFLOW_MCP_TOOLS` when the server starts. `adopt` writes a worker
config beside the project's own, in the git-ignored local layer (it records this machine's
launcher), and says how to start a worker with it. The tier hides schemas, never tools: every
tool stays callable by name.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..infra.fsio import replace_text

#: Where `adopt` writes it: the git-ignored local layer, since the launcher is machine-specific.
REL = ".ddflow/local/worker-mcp.json"
#: The variable the server reads its tier from (`DDFLOW_<SECTION>_<KNOB>` of `[mcp].tools`).
TIER_ENV = "DDFLOW_MCP_TOOLS"
#: `ENABLE_TOOL_SEARCH=auto:N` with N at or above this defers nothing for ddflow (auto:90 measured).
AUTO_NO_DEFER_PCT = 50


def worker_entry(entry: Mapping[str, Any], tier: str) -> dict[str, Any]:
    """``entry`` (how a client spawns ddflow) with the tier in its environment; the input is
    not changed."""
    out = dict(entry)
    out["env"] = {**dict(entry.get("env") or {}), TIER_ENV: tier}
    return out


def config_text(entry: Mapping[str, Any], tier: str) -> str:
    return json.dumps({"mcpServers": {"ddflow": worker_entry(entry, tier)}}, indent=2) + "\n"


def write(repo: Path, entry: Mapping[str, Any], tier: str) -> str:
    """Write the worker config; the action line adopt prints."""
    path = Path(repo) / REL
    text = config_text(entry, tier)
    if path.is_file() and path.read_text("utf-8") == text:
        return f"{REL} is current"
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_text(path, text)
    return f"wrote {REL}"


def recipe(rel: str = REL) -> str:
    """How to start a lane worker on that config (Claude Code), and the switch that decides
    whether the host defers tool schemas at all."""
    return (
        f"lane workers: `claude --mcp-config {rel} --strict-mcp-config` starts ddflow on the "
        "`core` tier and no other MCP server (the user's connectors cost ~12K more). Claude Code "
        "defers tool schemas on its own; `ENABLE_TOOL_SEARCH=false` (or a high `auto:N`) sends "
        "them all, which the core tier keeps small."
    )


def deferral_note(environ: Mapping[str, str], *, tier: str) -> str:
    """The doctor note when the host would send every ddflow schema: tool search switched off
    or set so high that nothing is deferred, while this project advertises the whole tool list.
    Empty on the `core`/`standard` tiers (already smaller) and on a default host."""
    if tier == "core":
        return ""
    value = environ.get("ENABLE_TOOL_SEARCH", "").strip().lower()
    high = (
        value.startswith("auto:")
        and value[5:].rstrip("%").isdigit()
        and int(value[5:].rstrip("%")) >= AUTO_NO_DEFER_PCT
    )
    if value not in ("false", "0", "off", "no") and not high:
        return ""
    return (
        f"ENABLE_TOOL_SEARCH={environ['ENABLE_TOOL_SEARCH']} makes the host send every ddflow schema "
        f"(~58K tokens on the `{tier}` tool tier): start workers on the `core` tier "
        f"(`[mcp].worker_tools`, {REL})"
    )
