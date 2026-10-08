"""The names this ddflow answers to, for the stale-reference scanner (`api.refs`).

The command table (an argparse tree) and the MCP tool registry are the surfaces', and the
modules that ask (`doctor`, in `commands` and in `tools`) are imported by the very modules
that hold them, so neither can be imported from here: each REGISTERS itself (`provide`)
when it is imported, and `mcp` imports `cli`, so either process holds both.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from typing import Any

from ..api import refs as A

_parser: Callable[[], argparse.ArgumentParser] | None = None
_tools: Mapping[str, Mapping[str, Any]] | None = None


def provide(
    *,
    parser: Callable[[], argparse.ArgumentParser] | None = None,
    tools: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Register the CLI parser factory and/or the MCP tool table."""
    global _parser, _tools  # noqa: PLW0603
    _parser = parser or _parser
    _tools = tools if tools is not None else _tools


def current_vocabulary() -> A.Vocabulary | None:
    """The commands, tools and old names of this running ddflow; None when the surfaces
    have not registered (nothing to check references against)."""
    if _parser is None or _tools is None:
        return None
    return A.vocabulary(_parser(), _tools)
