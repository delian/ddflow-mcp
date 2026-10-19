"""The README's table of supported agents is what the descriptors render (B-hx-guide)."""

from __future__ import annotations

from pathlib import Path

from ddflow.api.harness import agent_table
from ddflow.surfaces import harness_table as HT

README = Path(__file__).resolve().parents[1] / "README.md"


def test_the_readme_agents_table_is_what_the_descriptors_render():
    """When this fails a descriptor changed: run
    `uv run python -m ddflow.surfaces.harness_table README.md` (or `ddflow pins regenerate`)."""
    text = README.read_text("utf-8")
    assert text.count(f"<!-- ddflow:begin {HT.REGION} sha=") == 1
    assert not HT.hand_edited(text)
    assert HT.replace(text, HT.render()) == text, "README.md's agents table is stale"


def test_every_descriptor_has_a_row():
    block = HT.render()
    for _title, hid, *_rest in agent_table():
        assert f"| `{hid}` |" in block
