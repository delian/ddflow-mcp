"""Where `doctor` finds the names this ddflow answers to (for `api.refs`).

The command table (an argparse tree) and the MCP tool registry are the surfaces', and the
modules that ask (`doctor`, in `commands` and in `tools`) are imported BY the modules that
hold them, so none can be imported from here: each REGISTERS itself when it is imported --
the tool package its table, the CLI its parser (and the table with it). Stdlib only, so the
tool table can import it without pulling in the api (tests/test_clock_duration.py).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from typing import Any

Tools = Mapping[str, Mapping[str, Any]]
Parser = Callable[[], argparse.ArgumentParser]

_parser: Parser | None = None
_tools: Tools | None = None


def provide(*, parser: Parser | None = None, tools: Tools | None = None) -> None:
    """Register the CLI parser factory and/or the MCP tool table."""
    global _parser, _tools  # noqa: PLW0603
    _parser = parser or _parser
    _tools = tools if tools is not None else _tools


def sources() -> tuple[Parser | None, Tools | None]:
    """``(parser factory, tool table)``; None for what this process has not loaded. An MCP
    server loads only the tools: command words are checked by the CLI's `doctor`."""
    return _parser, _tools
