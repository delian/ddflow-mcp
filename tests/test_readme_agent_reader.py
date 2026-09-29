"""The README's "Instruction for an agent reader" is what an agent acts on when a user
says "read the README" — so every command in it has to exist, and it has to stay short.

An agent follows a shorthand literally. A misspelt subcommand or a tool that was renamed
sends it into an error it will try to work around, and a section that grows into a
second manual stops being a shorthand at all.
"""

from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path

from ddflow.surfaces import cli, mcp
from tests.conftest import run_cli

README = (Path(__file__).resolve().parents[1] / "README.md").read_text("utf-8")
HEADING = "## Instruction for an agent reader"


def _block(heading: str) -> str:
    """From `heading` to the next `## ` heading OUTSIDE a fenced code block — a `## `
    line inside a fence must not end the section early and hide what follows it."""
    lines = README.splitlines(keepends=True)
    start = next(i for i, ln in enumerate(lines) if ln.rstrip("\n") == heading)
    fenced = False
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("```"):
            fenced = not fenced
        elif not fenced and lines[i].startswith("## "):
            return "".join(lines[start:i])
    return "".join(lines[start:])


def _section() -> str:
    return _block(HEADING)


def _commands() -> list[str]:
    """Every `ddflow …` command the section shows: inline spans, and lines of fenced
    blocks, because a command in a code block is the one most likely to be pasted."""
    text = _section()
    inline = re.findall(r"`(ddflow [^`]+)`", text)
    fences = re.findall(r"^```[^\n]*\n(.*?)^```", text, flags=re.M | re.S)
    fenced = [
        ln.strip() for f in fences for ln in f.splitlines() if ln.strip().startswith("ddflow ")
    ]
    return inline + fenced


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def test_it_comes_before_the_introduction_and_is_in_the_contents():
    assert HEADING in README
    assert README.index(HEADING) < README.index("## Introduction")
    toc = _block("## Table of contents")
    assert "- [Instruction for an agent reader](#instruction-for-an-agent-reader)" in toc


def test_it_stays_a_shorthand():
    lines = [ln for ln in _section().splitlines() if ln.strip()]
    assert len(lines) <= 40, f"{len(lines)} non-blank lines: it is becoming a manual"


def test_every_cli_command_it_names_exists():
    top = _subcommands(cli.build_parser())
    named = {
        m.groups()
        for c in _commands()
        if (m := re.match(r"ddflow ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?", c))
    }
    assert named, "the section names no commands, so this test checks nothing"
    for cmd, sub in named:
        assert cmd in top, f"`ddflow {cmd}` is not a command"
        subs = _subcommands(top[cmd])
        if sub and subs:
            assert sub in subs, f"`ddflow {cmd} {sub}` is not a command"


def test_every_complete_command_it_writes_out_parses():
    """Read from the README, not restated here: a flag misspelt in the section has to
    fail this test, and a copy of the flags in the test would not."""
    parser = cli.build_parser()
    spans = _commands()
    # An elided span (`ddflow task add … --globs …`) is prose, not a command to run.
    complete = [s for s in spans if "…" not in s and " " in s.removeprefix("ddflow ")]
    assert any(" --" in s for s in complete), f"no command with flags found in {spans}"
    for span in complete:
        argv = shlex.split(re.sub(r"<[^>]*>", "x", span))[1:]
        try:
            parser.parse_args(argv)
        except SystemExit:
            raise AssertionError(f"`{span}` does not parse") from None


def test_every_mcp_tool_it_names_exists():
    named = set(re.findall(r"`(ddflow_[a-z_]+)`", _section()))
    assert named, "the section names no MCP tools, so this test checks nothing"
    missing = sorted(t for t in named if t not in mcp.TOOLS)
    assert not missing, f"not MCP tools: {missing}"


def test_the_driver_it_points_to_is_what_adopt_writes(repo: Path):
    """Checked by running the adopt the section tells the agent to run, not by looking
    for the file in THIS repository."""
    driver = "docs/ddflow/drivers/implement-phase.md"
    assert driver in _section()
    code, out, err = run_cli(repo, "adopt", "--agents", "claude", "--launch", "python")
    assert code == 0, out + err
    assert (repo / driver).is_file(), f"adopt did not write {driver}"
