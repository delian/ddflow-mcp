"""The MCP tool registry: every tool `surfaces/mcp.py` serves, split by domain.

`TOOLS` is assembled here from one module per domain, in the order the single table used
to hold them; `surfaces/mcp.py` (the JSON-RPC engine) imports and re-exports it, so
`from ddflow.surfaces.mcp import TOOLS` keeps working. A new tool goes into the module
for its domain, and into a tier in `tools/tiers.py`.
"""

from __future__ import annotations

from typing import Any

from . import (
    companions,
    decisions,
    flow,
    items,
    jobs,
    knowledge,
    lifecycle,
    maintenance,
    operations,
    queue,
    records,
    reporting,
    rules,
    setup,
    workflow,
)

#: Tool surface. Each entry maps an MCP tool onto an argv the CLI already understands,
#: so there is exactly one implementation of every operation.
#: (description, {property: (json_type, description, required)}, argv builder)
TOOLS: dict[str, dict[str, Any]] = {
    **lifecycle.TOOLS,
    **flow.TOOLS,
    **queue.TOOLS,
    **knowledge.TOOLS,
    **reporting.TOOLS,
    **decisions.TOOLS,
    **operations.TOOLS,
    **workflow.TOOLS,
    **companions.TOOLS,
    **records.TOOLS,
    **maintenance.TOOLS,
    **setup.TOOLS,
    **items.TOOLS,
    **jobs.TOOLS,
    **rules.TOOLS,
}

#: The add tools: each takes the answer to the duplicate check (`api/_dedupe.py`).
ADD_TOOLS = (
    "ddflow_phase_add",
    "ddflow_task_add",
    "ddflow_bug_found",
    "ddflow_lesson_add",
    "ddflow_decision_add",
    "ddflow_research_add",
    "ddflow_memory_add",
)

DEDUPE_PROPERTIES: dict[str, tuple[str, str, bool]] = {
    "relation": (
        "string",
        "Answer to a 'possible duplicate' refusal: new | extends:ID | duplicate_of:ID | "
        "related:ID (the refusal lists candidates and options). Omit at first.",
        False,
    ),
    "check_only": (
        "boolean",
        "Dry run: write nothing, return the `candidates` this add would be refused for.",
        False,
    ),
}

for _name in ADD_TOOLS:
    TOOLS[_name]["properties"].update(DEDUPE_PROPERTIES)
