"""The contributor guide's worked example parses, and the README's agent table is what the
descriptors render (B-hx-guide)."""

from __future__ import annotations

import re
from pathlib import Path

from ddflow.services import harnessreg as HR

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "ddflow" / "adding-a-harness.md"


def _example() -> str:
    m = re.search(r"```toml acmecli\.toml\n(.*?)```", GUIDE.read_text("utf-8"), re.S)
    assert m, "the guide lost its worked example"
    return m.group(1)


def test_the_worked_example_is_a_valid_descriptor():
    h = HR.parse(_example(), "acmecli")
    assert h.id == "acmecli" and h.title == "Acme CLI"
    assert h.hooks.style == "claude" and "stop" in h.hooks.events


def test_the_guide_names_every_hook_style_and_shape_the_loader_accepts():
    text = GUIDE.read_text("utf-8")
    row = next(r for r in text.splitlines() if r.startswith("| `[hooks]` |"))
    for style in HR.HOOK_STYLES:
        assert f"`{style}`" in row, style
    for shape in HR.SHAPES - {HR.SHAPE_NONE}:
        assert f"`{shape}`" in text, shape


def test_the_guide_stays_small():
    assert GUIDE.stat().st_size < 20_000


def test_a_descriptor_without_an_mcp_section_is_refused_not_rendered():
    """`agent_table` may read `h.mcp.path` unguarded because the loader never yields a
    harness without `[mcp]`: `shape = "none"` with `path = ""` is the absent form."""
    import pytest

    text = _example().replace('[mcp]\npath = ".acme/mcp.json"\nshape = "mcpServers"\n', "")
    with pytest.raises(HR.DescriptorError):
        HR.parse(text, "acmecli")
