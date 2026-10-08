"""The `[mcp]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob

#: The tool tiers `[mcp].tools` accepts. Kept here, not imported from the MCP surface, so
#: config validation does not depend on the surface above it; `tests/test_mcp_tool_tiers.py`
#: asserts the two lists agree.
MCP_TOOL_TIERS = ("core", "standard", "all")
#: `[mcp].output_schemas`.
MCP_OUTPUT_SCHEMAS = ("off", "on")


@declare("mcp")
@dataclass
class McpConfig:
    """The MCP server's own knobs. A START-TIME choice: read once when a connection starts."""

    tools: str = knob(
        "all",
        doc="Which tools `tools/list` advertises: `core` (the ~30 tools of the daily loop: brief, next, claim, gates, complete, merge, recall, bugs, lessons, decisions, sessions, setup, help), `standard` (core plus the commonly used rest) or `all` (default, every tool). A tool outside the tier is NOT removed: it stays callable by name, and `ddflow_help` and the connection instructions name what the tier hides. Read once at server start, so change it and restart the server; `listChanged` stays false. Set it to cut the ~90 KB tool list a client without deferred tool search pays in context every session (core is under 40 KB). A newer release's tier in a config file is tolerated (see below).",
        choices=MCP_TOOL_TIERS,
        strictest=("all", "no safety dimension; every tool advertised, as without the knob"),
    )

    output_schemas: str = knob(
        "off",
        doc='Whether `tools/list` declares an `outputSchema` for every tool whose result is a JSON object, and a call returns that object also as `structuredContent` (MCP 2025-06-18 and later; an older client is served as with `off`). `off` (default): the result is the JSON text in the first content block only, and `tools/list` is as small as before. `on`: each object tool lists `{"type": "object", "properties": {"schema": {"const": "<tool>@<n>"}, <its declared fields>: {}}}` -- additive, nothing required -- and a bare-array tool lists its schema under `_meta["ddflow/outputSchema"]`, since an `outputSchema` must be an object. Costs about 16 KB more `tools/list` and every object result is sent twice (the text block and `structuredContent`). Every object result carries its `schema` tag either way. Read once at server start, so change it and restart the server.',
        choices=MCP_OUTPUT_SCHEMAS,
        strictest=("off", "no safety dimension; the smaller, unchanged tool list and result"),
    )
