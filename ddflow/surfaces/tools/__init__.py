"""The MCP tool registry: every tool `surfaces/mcp.py` serves.

`TOOLS` is generated here from the declarations (`surfaces/declared/`) and the few
hand-written entries of this package, in the order of `tools/order.py`; `surfaces/mcp.py`
(the JSON-RPC engine) imports and re-exports it, so `from ddflow.surfaces.mcp import TOOLS`
keeps working. A new tool is declared, named in `tools/order.py` and put in a tier in
`tools/tiers.py`.
"""

from __future__ import annotations

from itertools import chain
from typing import Any

from ..declared import cadence as _cadence
from ..declared import export as _export
from ..declared import flow as _flow
from ..declared import hooks as _hooks
from ..declared import knowledge as _knowledge
from ..declared import lifecycle as _lifecycle
from ..declared import memory as _memory
from ..declared import queue as _queue
from ..declared import records as _records
from ..declared import reporting as _reporting
from ..declared import review as _review
from ..declared import rules as _rules
from ..declared import setup as _setup
from ..registry import by_tool
from ..vocabulary import provide
from . import items, operations
from . import reporting as onboarding
from .order import TOOL_ORDER

#: Every declared command, by tool name.
DECLARED = by_tool(
    chain.from_iterable(
        f.COMMANDS
        for f in (
            _knowledge, _records, _queue, _lifecycle, _rules, _review, _setup, _reporting,
            _hooks, _flow, _memory, _export, _cadence,
        )
    )
)  # fmt: skip

#: The hand-written entries: tools no declaration covers yet (their families migrate them).
HAND_WRITTEN: dict[str, dict[str, Any]] = {
    **operations.TOOLS,
    **items.TOOLS,
    **onboarding.TOOLS,
}

#: Tool surface, in `TOOL_ORDER`. Each entry maps an MCP tool onto the api call that serves it
#: (description, {property: (json_type, description, required)}, api, payload).
TOOLS: dict[str, dict[str, Any]] = {
    name: DECLARED[name].tool_entry() if name in DECLARED else HAND_WRITTEN[name]
    for name in TOOL_ORDER
}
if len(TOOL_ORDER) != len(set(TOOL_ORDER)) or len(TOOLS) != len(DECLARED) + len(HAND_WRITTEN):
    raise RuntimeError("tools/order.py lists a tool twice or leaves one out")

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

provide(tools=TOOLS)  # what `doctor` checks tool names against

for _name in ADD_TOOLS:
    TOOLS[_name]["properties"].update(DEDUPE_PROPERTIES)
