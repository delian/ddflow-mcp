"""The agent instructions teach the no-duplicates workflow (BL-no-duplicates).

Check before filing, answer a duplicate refusal, prefer extending an open unclaimed record,
and close a duplicate bug only once its original is fixed. Every command the text names
must exist.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "ddflow" / "templates" / "drivers" / "implement-phase.md"
MANUAL = ROOT / "ddflow" / "templates" / "prompts" / "help" / "manual.md"


def _section(text: str, start: str, stop: str) -> str:
    return text.split(start, 1)[1].split(stop, 1)[0]


def test_the_driver_teaches_closing_a_duplicate_bug():
    s = _section(DRIVER.read_text("utf-8"), "### 2e.", "### 2f.")
    assert "ddflow similar" in s
    assert "--merge-into" in s or "append" in s
    assert "bug duplicate" in s and "--of" in s
    assert "open_duplicates" in s or "open duplicates" in s
    assert "only once" in s, "a duplicate closes only once its original is fixed"


def test_the_manual_teaches_closing_a_duplicate_bug():
    s = MANUAL.read_text("utf-8")
    assert "ddflow_similar" in s
    assert "duplicate" in s and "action=duplicate" in s.replace("`", "").replace(" ", "")
    assert "open_duplicates" in s


def test_every_ddflow_command_the_dedupe_text_names_parses():
    """Each backticked `ddflow ...` line of section 2e is run through the real parser, so a
    misspelled subcommand or flag fails here (a `<PLACEHOLDER>` stands for any word)."""
    from ddflow.surfaces.cli import build_parser

    text = _section(DRIVER.read_text("utf-8"), "### 2e.", "### 2f.")
    parser = build_parser()
    seen = 0
    for line in re.findall(r"`(ddflow [^`]+)`", text):
        argv = re.sub(r"<[A-Z]+>", "X1", line).replace('"<the text>"', "text").split()[1:]
        argv = [a for a in argv if a not in ("...",)]
        if not argv or argv[0] not in ("bug", "similar", "lesson", "link"):
            continue
        try:
            parser.parse_args(["--agent", "t", *argv])
        except SystemExit as e:  # argparse exits 2 on an unknown command or flag
            raise AssertionError(f"`{line}` does not parse") from e
        seen += 1
    assert seen >= 3


def test_the_manual_names_a_real_tool_action_and_argument(repo, monkeypatch):
    from ddflow.surfaces.mcp import Server

    monkeypatch.setenv("DDFLOW_MCP_TOOLS", "families")
    run_cli(repo, "init")
    reply = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    spec = next(t for t in reply["result"]["tools"] if t["name"] == "ddflow_bug")
    props = spec["inputSchema"]["properties"]
    assert "duplicate" in props["action"]["enum"]
    assert "of" in props
