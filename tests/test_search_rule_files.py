"""`ddflow search` over a rule file that has no definition event (Bbb67fca459).

A rule file arrives without one when it was written by hand or came in with a pull. It
stamps `created`/`updated` as quoted strings, so the rule read back has a `str` stamp, and
the search called `.isoformat()` on it: every search in a project holding one crashed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services.rules import Rule, RulesStorage


def test_search_finds_a_rule_file_with_no_definition_event(repo: Path) -> None:
    code, _out, err = run_cli(repo, "init")
    assert code == 0, err
    RulesStorage(repo).add(
        Rule(id="r-hand-written", title="Hand written", content="Turnips are never pickled.")
    )
    code, out, err = run_cli(repo, "--json", "search", "turnips")
    assert code == 0, err
    rows = json.loads(out)["rows"]
    assert any(row.get("id") == "r-hand-written" for row in rows), rows
