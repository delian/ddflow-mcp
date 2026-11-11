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


def test_every_ddflow_command_the_dedupe_text_names_exists():
    from ddflow.surfaces.cli import build_parser

    text = _section(DRIVER.read_text("utf-8"), "### 2e.", "### 2f.")
    helptext = build_parser().format_help()
    for top in set(re.findall(r"`ddflow ([a-z][a-z-]*)", text)):
        assert top in helptext, f"`ddflow {top}` is named but is not a command"
