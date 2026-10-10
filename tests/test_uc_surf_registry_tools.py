"""The generated tool table (B-uc-surf-registry): `tools/order.py` names every tool once."""

from __future__ import annotations

from ddflow.surfaces import tools
from ddflow.surfaces.tools.order import TOOL_ORDER


def test_the_order_names_every_declared_and_hand_written_tool_exactly_once():
    assert len(TOOL_ORDER) == len(set(TOOL_ORDER))
    assert set(TOOL_ORDER) == set(tools.DECLARED) | set(tools.HAND_WRITTEN)
    assert not set(tools.DECLARED) & set(tools.HAND_WRITTEN)
    assert list(tools.TOOLS) == list(TOOL_ORDER)
