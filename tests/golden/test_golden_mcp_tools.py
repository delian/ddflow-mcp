"""Golden: the MCP `tools/list` an agent receives at the handshake, in order.

The names first, so an added, removed or reordered tool is one short diff; then every
tool's full definition (description and input schema), keyed by name.
"""

# ruff: noqa: F811 -- the goldenfix fixtures are imported, then named as parameters
from __future__ import annotations

import json
from pathlib import Path

import pytest
from goldenfix import _pinned_environment, project  # noqa: F401 -- fixtures

from ddflow.surfaces.mcp import Server


def _tools(root: Path) -> list[dict]:
    reply = Server(root).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return reply["result"]["tools"]


@pytest.fixture
def tools(project: Path) -> list[dict]:
    return _tools(project)


def test_the_tool_names_in_order(tools, snapshot):
    assert [t["name"] for t in tools] == snapshot


def test_every_tool_definition(tools, snapshot):
    assert {t["name"]: json.dumps(t, indent=1, ensure_ascii=False) for t in tools} == snapshot
