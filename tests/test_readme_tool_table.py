"""The README's MCP tool table and every tool count it states match the registry
(B-uni-cmd-core)."""

from __future__ import annotations

import re
from pathlib import Path

from ddflow.api import readme_tools as RT
from ddflow.services.help import grouped_tools
from ddflow.surfaces import tool_table as TT
from ddflow.surfaces.mcp import TOOLS
from ddflow.surfaces.tools.tiers import CORE_TOOLS, FULL_ONLY_TOOLS, STANDARD_EXTRA_TOOLS

README = Path(__file__).resolve().parents[1] / "README.md"


def _readme() -> str:
    return README.read_text("utf-8")


def test_the_readme_tool_table_is_what_the_registry_renders():
    """When this fails a tool, its description, its group or its tier changed: run
    `uv run python -m ddflow.surfaces.tool_table README.md` and commit."""
    readme = _readme()
    assert readme.count(f"<!-- ddflow:begin {RT.REGION} sha=") == 1
    assert readme.count(RT.END) == 1
    assert not RT.hand_edited(readme)
    assert RT.replace(readme, TT.render()) == readme, (
        "README.md's tool table is stale: `uv run python -m ddflow.surfaces.tool_table README.md`"
    )


def test_one_row_per_tool_and_the_counts_are_the_registry_s():
    block = TT.render()
    rows = [line for line in block.splitlines() if line.startswith("| ") and "`ddflow_" in line]
    names = [re.search(r"`(ddflow_[a-z_]+)`", r).group(1) for r in rows]
    assert sorted(names) == sorted(TOOLS) and len(set(names)) == len(TOOLS)
    assert f"All {len(TOOLS)} MCP tools" in block
    assert f"{len(CORE_TOOLS)} in the `core` tier" in block
    assert f"{len(CORE_TOOLS | STANDARD_EXTRA_TOOLS)} in `standard`" in block


def test_every_tool_is_in_a_help_group_and_a_tier():
    assert not [t for t, _ in grouped_tools(TOOLS) if t.startswith("Unmapped")]
    assert {TT.tier_of(t) for t in TOOLS} == {"core", "standard", "all"}
    assert {t for t in TOOLS if TT.tier_of(t) == "all"} == FULL_ONLY_TOOLS


def test_summary_is_the_first_sentence_cut_at_a_word_and_pipe_safe():
    assert RT.summary("One thing. Another thing.") == "One thing."
    assert RT.summary("a | b") == "a \\| b"
    long = "word " * 60
    out = RT.summary(long)
    assert len(out) <= RT.SUMMARY_MAX and out.endswith("…")


def test_hand_edit_is_detected_and_a_missing_region_refused():
    import pytest

    block = TT.render()
    assert not RT.hand_edited(block)
    assert RT.hand_edited(block.replace("| core |", "| all |", 1))
    with pytest.raises(ValueError):
        RT.replace("no region here", block)


def test_main_rewrites_a_stale_table_and_refuses_a_hand_edited_one(tmp_path, capsys):
    stale = tmp_path / "README.md"
    stale.write_text(f"intro\n\n<!-- ddflow:begin {RT.REGION} sha=000000000000 -->\nx\n{RT.END}\n")
    assert TT.main([str(stale)]) == 3  # the body does not match its sha: edited by hand
    assert TT.main([str(stale), "--force"]) == 0
    assert TT.main([str(stale)]) == 0
    assert "already current" in capsys.readouterr().out
    assert stale.read_text() == "intro\n\n" + TT.render() + "\n"
