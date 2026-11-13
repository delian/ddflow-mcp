"""Help text must state what the tools enforce (bugs Be097545791, B82341869ed, B1284210c1e)."""

from __future__ import annotations

import argparse
from pathlib import Path

from ddflow.surfaces import cli, mcp

ROOT = Path(__file__).resolve().parent.parent


def _subparser(parser: argparse.ArgumentParser, *path: str) -> argparse.ArgumentParser:
    for name in path:
        action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        parser = action.choices[name]
    return parser


def _help_of(sub: argparse.ArgumentParser, flag: str) -> str:
    return next(a.help or "" for a in sub._actions if flag in a.option_strings)


def test_gate_record_help_names_the_reason_requirement():
    for name in ("record", "skip"):
        sub = _subparser(cli.build_parser(), "gate", name)
        for outcome in ("failed", "unavailable", "partial", "skipped"):
            assert outcome in _help_of(sub, "--reason"), (name, outcome)
        assert "--reason" in _help_of(sub, "--outcome")


def test_next_help_names_exit_1_for_unknown_phase():
    sub = _subparser(cli.build_parser(), "next")
    text = (sub.description or "") + " ".join(a.help or "" for a in sub._actions)
    assert "exit 1" in text
    for path in (
        ROOT / "ddflow/templates/drivers/implement-phase.md",
        ROOT / "docs/ddflow/drivers/implement-phase.md",
    ):
        assert "exit 1" in path.read_text().split("### 2b.")[0].split("### 2a.")[1], path


def test_mcp_show_says_it_accepts_bug_ids():
    spec = mcp.TOOLS["ddflow_show"]
    assert "bug" in spec["description"].lower()
    assert "bug" in spec["properties"]["id"][1].lower()


def test_mcp_gate_schemas_name_why_reason_is_required():
    """B80ea725095: an MCP caller of ddflow_gate recording partial/unavailable must see in the
    schema that --reason is required for those outcomes, not skip's 'does not apply HERE' text
    (Be097545791 fixed the CLI help; the MCP schema kept the skip-only framing)."""
    from ddflow.surfaces.tools.family_schema import family_entry

    direct = mcp.TOOLS["ddflow_gate_record"]["properties"]["reason"][1]
    for outcome in ("failed", "unavailable", "partial", "skipped"):
        assert outcome in direct, outcome

    family = family_entry("ddflow_gate")["inputSchema"]["properties"]["reason"]["description"]
    for outcome in ("failed", "unavailable", "partial", "skipped"):
        assert outcome in family, outcome
