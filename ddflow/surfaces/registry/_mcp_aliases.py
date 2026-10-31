"""The MCP side of the alias machinery (D-compat).

Resolving a tool name that is an alias, hinting at an unknown tool or argument, and
moving a deprecated argument spelling onto its current name.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ._aliases import Alias, _did_you_mean, closest

# -- the MCP side of aliases ----------------------------------------------------------


def resolve_tool(tools: Mapping[str, Mapping[str, Any]], name: str) -> tuple[str, Alias | None]:
    """``(tool name, the alias used)``: ``name`` itself when it is a tool, the tool an alias
    names otherwise, and ``("", None)`` for a name that is neither."""
    if name in tools:
        return name, None
    for tool, spec in tools.items():
        for alias in spec.get("aliases", ()):
            if alias.old == name:
                return tool, alias
    return "", None


def unknown_tool_hint(tools: Mapping[str, Any], name: str) -> str:
    """`` Did you mean 'ddflow_x'?`` for a tool call naming nothing, or ""."""
    return _did_you_mean(name, tools)


def rename_args(
    spec: Mapping[str, Any], args: Mapping[str, Any]
) -> tuple[dict[str, Any], list[Alias], str]:
    """``(args under their current names, the aliases used, an error or "")``.

    An argument spelled with an old name is moved to the current one; giving both is an
    error (which one is meant?).
    """
    out = dict(args)
    used: list[Alias] = []
    for old, alias in (spec.get("arg_aliases") or {}).items():
        if old not in out:
            continue
        if alias.new in out:
            return out, used, f"both '{alias.new}' and its deprecated name '{old}' were given"
        out[alias.new] = out.pop(old)
        used.append(alias)
    return out, used, ""


def unknown_arg_hint(known: Iterable[str], unknown: Iterable[str]) -> str:
    """`` Did you mean 'a' (for 'b')?`` for the unknown arguments that have a near name."""
    known = list(known)  # read once per unknown name
    pairs = [(u, closest(u, known)) for u in unknown]
    said = [f"'{near}' (for '{u}')" for u, near in pairs if near]
    return f" Did you mean {', '.join(said)}?" if said else ""

