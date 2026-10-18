"""The MCP tool registry: every tool `surfaces/mcp.py` serves.

`TOOLS` is generated here from the declarations (`surfaces/declared/`) and the few
hand-written entries of this package, in the order of `tools/order.py`; `surfaces/mcp.py`
(the JSON-RPC engine) imports and re-exports it, so `from ddflow.surfaces.mcp import TOOLS`
keeps working. A new tool is declared in a new `declared/<family>.py` with a `COMMANDS` tuple and its
`tier`; it is found by convention and listed after the legacy `tools/order.py` entries.
"""

from __future__ import annotations

from typing import Any

from ..declared import all_commands
from ..declared import plan as plan
from ..registry import by_tool
from ..vocabulary import provide
from . import items, operations
from . import reporting as onboarding
from .order import TOOL_ORDER as LEGACY_ORDER

#: Every declared command, by tool name.
DECLARED = by_tool(all_commands(tools=True))

#: The hand-written entries: tools no declaration covers yet (their families migrate them).
HAND_WRITTEN: dict[str, dict[str, Any]] = {
    **operations.TOOLS,
    **items.TOOLS,
    **onboarding.TOOLS,
}

#: Tool surface, in `TOOL_ORDER`. Each entry maps an MCP tool onto the api call that serves it
#: (description, {property: (json_type, description, required)}, api, payload).
ORDER: tuple[str, ...] = (
    *LEGACY_ORDER,
    *(n for n in (*DECLARED, *HAND_WRITTEN) if n not in LEGACY_ORDER),
)
TOOLS: dict[str, dict[str, Any]] = {
    name: DECLARED[name].tool_entry() if name in DECLARED else HAND_WRITTEN[name] for name in ORDER
}
if len(LEGACY_ORDER) != len(set(LEGACY_ORDER)) or len(TOOLS) != len(DECLARED) + len(HAND_WRITTEN):
    raise RuntimeError("tools/order.py lists a tool twice, or names one nothing declares")

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
        "related:ID. Omit at first.",
        False,
    ),
    "check_only": (
        "boolean",
        "Dry run: write nothing, return the `candidates`.",
        False,
    ),
}

provide(tools=TOOLS)  # what `doctor` checks tool names against

for _name in ADD_TOOLS:
    TOOLS[_name]["properties"].update(DEDUPE_PROPERTIES)
