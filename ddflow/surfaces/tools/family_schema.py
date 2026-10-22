"""Family tool schemas and routing (MTD-family-schema, D-mcp-tool-families).

A family is ONE advertised tool with an ``action`` enum. Its ``inputSchema`` is the union of
its members' arguments, each name once (the longest description wins), ``as_agent`` once, and
only ``action`` required: what an action needs is checked per action, by the member's own
declaration, with errors that name the action (`route`, then the engine's `_check_call`). The
description lists the actions and the arguments each one requires, so a model reads the
per-action shape without the union's noise. Everything is derived from `TOOLS` and
`families.FAMILIES`; nothing here lists a tool.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from ..registry import AS_AGENT, AS_AGENT_SPEC, Param, params_schema
from . import TOOLS
from .families import FAMILIES, Family

__all__ = ("family_entry", "family_listing", "member_props", "missing_required", "route")

ACTION = "action"
#: A member that has an argument of its own called ``action`` (queue, ci, prompts, hooks,
#: export) is given it as ``op`` in its family, where ``action`` picks the member.
OP = "op"

#: One clause per family, after the action list.
_BLURB = {
    "gate": "Quality gates of a work item",
    "bug": "Bug records",
    "decision": "Architectural decisions",
    "memory": "Lessons, memories, recall and record links",
    "rule": "Project rules",
    "session": "The session log",
    "job": "Background jobs",
    "ship": "Versions and promotions",
    "workflow": "Workflows and the flow",
    "reviewers": "Reviews, reviewers, companions and pull requests",
    "task": "Tasks, phases and research notes",
    "lease": "Leases on items",
    "view": "Read the queue, history and reports",
    "doctor": "Health, repair and hooks",
    "admin": "Setup, configuration, import, export and upgrade",
}


class Routed(NamedTuple):
    """A family call resolved to its member: the tool, its arguments, and a display name."""

    tool: str
    args: dict[str, Any]
    display: str


def member_props(tool: str) -> dict[str, tuple[str, str, bool]]:
    props = dict(TOOLS[tool]["properties"])
    if not TOOLS[tool].get("identify"):
        props[AS_AGENT] = AS_AGENT_SPEC
    return props


def _union(family: Family) -> dict[str, tuple[set[str], str]]:
    """``{argument: (types, description)}`` over the members, the longest description kept.

    A member's own ``action`` argument is ``op`` here, its description naming the action it
    belongs to (the members' meanings differ, so they are joined, not picked between)."""
    union: dict[str, tuple[set[str], str]] = {}
    for act, tool in family.members.items():
        for arg, (kind, text, _req) in member_props(tool).items():
            if arg == ACTION:
                types, said = union.get(OP, (set(), ""))
                union[OP] = (types | {kind}, f"{said} {act}: {text}".strip())
                continue
            types, best = union.get(arg, (set(), ""))
            union[arg] = (types | {kind}, text if len(text) > len(best) else best)
    return union


def _requires(tool: str) -> list[str]:
    """What the member requires, by the name its family gives each argument (``op``)."""
    return [
        OP if n == ACTION else n for n, (_t, _d, req) in TOOLS[tool]["properties"].items() if req
    ]


def missing_required(routed: Routed) -> list[str]:
    """The arguments the routed member requires and the call lacks, by their family names.

    Missing is absent, ``None`` or ``""`` -- what the engine's own check calls missing -- while
    ``0`` and ``False`` are values."""
    props = TOOLS[routed.tool]["properties"]
    return [
        OP if n == ACTION else n
        for n, (_t, _d, req) in props.items()
        if req and routed.args.get(n) in (None, "")
    ]


def _description(family: Family) -> str:
    acts = ", ".join(
        f"{a}({','.join(r)})" if (r := _requires(t)) else a for a, t in family.members.items()
    )
    noun = family.name.removeprefix("ddflow_")
    return f"{_BLURB[noun]}. Set `action`; required arguments in brackets: {acts}."


def family_entry(name: str) -> dict[str, Any]:
    """The ``tools/list`` entry of family ``name``."""
    family = FAMILIES[name]
    union = _union(family)
    params = tuple(
        Param(n, type="string" if len(types) > 1 else next(iter(types)), help=text)
        for n, (types, text) in union.items()
    )
    schema = params_schema(params)
    for n, (types, _text) in union.items():
        if len(types) > 1:
            schema["properties"][n]["type"] = sorted(types)
    schema["properties"][ACTION] = {
        "type": "string",
        "enum": list(family.members),
        "description": "What to do; see the tool description for each action's arguments.",
    }
    schema["required"] = [ACTION]
    return {"name": name, "description": _description(family), "inputSchema": schema}


def family_listing() -> list[dict[str, Any]]:
    """The ``tools/list`` entries of the 15 family tools, in `FAMILIES` order."""
    return [family_entry(n) for n, f in FAMILIES.items() if not f.single]


def route(name: str, args: dict[str, Any]) -> Routed | str | None:
    """Resolve a family call ``name`` + ``args`` to its member.

    ``None``: ``name`` is no family (a member's own legacy name, a hot single, or an unknown
    tool), or a self-named family tool (``ddflow_doctor``, ``ddflow_workflow``) called with no
    ``action``, which is the legacy tool and stays so. A string: why the call cannot be routed --
    no or an unknown action, a required argument missing, or one the action does not take, each
    said with the action it was asked of.
    """
    family = FAMILIES.get(name)
    if family is None or family.single:
        return None
    action = args.get(ACTION)
    if action is None and name in TOOLS:
        return None
    acts = ", ".join(family.members)
    if not isinstance(action, str) or not action:
        return f"{name}: missing required argument: action. Actions: {acts}"
    tool = family.members.get(action)
    if tool is None:
        return f"{name}: unknown action {action!r}. Actions: {acts}"
    rest = {k: v for k, v in args.items() if k != ACTION}
    if OP in rest and ACTION in TOOLS[tool]["properties"]:
        rest[ACTION] = rest.pop(OP)
    routed = Routed(tool, rest, f"{name} action={action}")
    return _problem(routed) or routed


def _problem(routed: Routed) -> str:
    """Why the routed call lacks what its member needs ("" when it has it): a required argument
    missing, or one the member does not take, each said with the action it was asked of."""
    if missing := missing_required(routed):
        return f"{routed.display}: missing required argument(s): {', '.join(missing)}"
    known = set(member_props(routed.tool))
    known |= set(TOOLS[routed.tool].get("deprecated") or {})
    if unknown := sorted(set(routed.args) - known):
        return (
            f"{routed.display}: unknown argument(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(OP if k == ACTION else k for k in known))}"
        )
    return ""
