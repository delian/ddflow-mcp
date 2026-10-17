"""`python -m ddflow.surfaces.command_table [README.md] [--force]`: rewrite the README's list of
every CLI command from the parser (DX-pins-uncovered).

The README must name every top-level command (tests/test_readme_catchup.py), and a command
added in one branch used to need a prose line written by hand in the file every other branch
also edits. The `README/commands` region is rendered from the parser, so a new command is
re-recorded by the pin recipe instead of merged by hand."""

from __future__ import annotations

import argparse
import re
import sys

from ..views.readme_region import Region
from . import cli

REGION = "README/commands"
#: A summary longer than this is cut at a word and ends in an ellipsis.
SUMMARY_MAX = 110


def commands() -> list[tuple[str, str]]:
    """(name, one-line summary) of each top-level command, in the parser's order."""
    parser = cli.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            helps = {c.dest: c.help or "" for c in action._choices_actions}
            rows = []
            for name, sub in action.choices.items():
                # A command registered without `help=` has none in the parent's listing: its
                # own description stands in, so that none is left out of the list.
                text = " ".join((helps.get(name) or sub.description or "").split())
                text = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
                if len(text) > SUMMARY_MAX:
                    text = text[: SUMMARY_MAX - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
                rows.append((name, text.replace("|", "\\|")))
            return rows
    return []


def render(_readme: str = "") -> str:
    """The region, markers included: the count, then one row per command."""
    rows = commands()
    body = "\n".join(
        [
            f"<details><summary>All {len(rows)} commands</summary>",
            "",
            "| Command | What it does |",
            "|---|---|",
            *[f"| `ddflow {name}` | {text} |" for name, text in rows],
            "",
            "</details>",
        ]
    )
    return _REGION.block(re.sub(r"[ \t]+\n", "\n", body))


_REGION = Region(REGION, "command list", render)
replace = _REGION.replace
hand_edited = _REGION.hand_edited


def main(argv: list[str]) -> int:
    return _REGION.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
