"""Bec642db4a9: `research` had `add` and `list` but no `show` verb.

The record kinds read one record back with `show` -- `bug show`, `decision show`,
`rule show` -- but a research note is only reachable through the generic
`ddflow show ID`. This pins the parity: `research` must expose `show`, and it
must read ONE note back by id.
"""

from __future__ import annotations

import argparse

from ddflow.surfaces.cli import build_parser
from tests._cli_helpers import run_cli


def _research_subparser() -> argparse.ArgumentParser:
    parser = build_parser()
    (subs,) = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    return subs.choices["research"]


def test_research_has_a_show_verb_like_decision_and_rule():
    research = _research_subparser()
    verb = next(a for a in research._actions if a.dest == "verb")
    assert "show" in verb.choices


def test_research_show_takes_a_positional_id_and_routes_to_show():
    ns = _research_subparser().parse_args(["show", "R1"])
    assert ns.verb == "show"
    assert ns.research_id == "R1"


def test_research_show_reads_one_note(repo):
    run_cli(repo, "init")
    code, _, err = run_cli(
        repo, "research", "add", "--id", "R1", "--question", "q one", "--verdict", "THEORETICAL"
    )
    assert code == 0, err
    code, out, err = run_cli(repo, "research", "show", "R1")
    assert code == 0, err
    assert "R1 [research]" in out
