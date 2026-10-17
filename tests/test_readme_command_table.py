"""The README's list of every command is what the parser renders (DX-pins-uncovered)."""

from __future__ import annotations

import re
from pathlib import Path

from ddflow.surfaces import command_table as CT

README = Path(__file__).resolve().parents[1] / "README.md"


def test_the_readme_command_list_is_what_the_parser_renders():
    """When this fails a command, or its help line, changed: run
    `uv run python -m ddflow.surfaces.command_table README.md` (or `ddflow pins regenerate`)."""
    text = README.read_text("utf-8")
    assert text.count(f"<!-- ddflow:begin {CT.REGION} sha=") == 1
    assert not CT.hand_edited(text)
    assert CT.replace(text, CT.render()) == text, "README.md's command list is stale"


def test_every_top_level_command_has_a_row():
    rows = CT.commands()
    assert len(rows) >= 60 and len({n for n, _ in rows}) == len(rows)
    block = CT.render()
    for name, _ in rows:
        assert f"| `ddflow {name}` |" in block
    assert f"All {len(rows)} commands" in block


def test_a_cell_never_breaks_the_table():
    for _name, text in CT.commands():
        assert "\n" not in text and not re.search(r"(?<!\\)\|", text)
