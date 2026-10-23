"""The tool surfaces the eval compares, and how a model's call on each is resolved to a member."""

from __future__ import annotations

import json
from typing import Any

from ddflow.surfaces.registry import properties_schema
from ddflow.surfaces.tools import TOOLS, families, family_schema

SURFACES = ("flat", "families")


def _function(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def flat_tools() -> list[dict[str, Any]]:
    """Every tool as the flat `tools/list` serves it."""
    return [
        _function(t, TOOLS[t]["description"], properties_schema(family_schema.member_props(t)))
        for t in sorted(TOOLS)
    ]


def family_tools() -> list[dict[str, Any]]:
    """The 9 hot singles and the 15 families."""
    singles = [
        _function(t, TOOLS[t]["description"], properties_schema(family_schema.member_props(t)))
        for t in families.HOT_SINGLES
    ]
    fams = [
        _function(e["name"], e["description"], e["inputSchema"])
        for e in family_schema.family_listing()
    ]
    return sorted(singles + fams, key=lambda f: f["function"]["name"])


def tools_for(surface: str) -> list[dict[str, Any]]:
    return flat_tools() if surface == "flat" else family_tools()


def size(tools: list[dict[str, Any]]) -> int:
    """Compact bytes of the function definitions the model is shown."""
    return len(json.dumps(tools, separators=(",", ":")).encode())


def resolve(surface: str, name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    """``(member tool, its arguments, an error)`` for a call on ``surface``.

    Flat: the name is the member. Families: the engine's own `route`, so what the eval grades
    is what the server would run; a name that is a hot single or a self-named tool is itself."""
    if surface == "flat":
        return (name, args, "") if name in TOOLS else ("", args, f"unknown tool {name}")
    routed = family_schema.route(name, args)
    if isinstance(routed, str):
        return "", args, routed
    if routed is not None:
        return routed.tool, routed.args, ""
    return (name, args, "") if name in TOOLS else ("", args, f"unknown tool {name}")
