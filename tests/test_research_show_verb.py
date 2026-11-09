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


def test_research_show_takes_id_from_the_flag_too(repo):
    """`research show --id R1` reads the note; the positional is accepted but does not
    clobber a parsed `--id`."""
    run_cli(repo, "init")
    run_cli(
        repo, "research", "add", "--id", "R1", "--question", "q one", "--verdict", "THEORETICAL"
    )
    code, out, err = run_cli(repo, "research", "show", "--id", "R1")
    assert code == 0, err
    assert "R1 [research]" in out


def test_research_show_refuses_an_id_given_both_ways(repo):
    """Giving the id both positionally and via --id is ambiguous: refuse, don't discard
    the explicit --id."""
    run_cli(repo, "init")
    run_cli(
        repo, "research", "add", "--id", "R1", "--question", "q one", "--verdict", "THEORETICAL"
    )
    code, _, err = run_cli(repo, "research", "show", "R2", "--id", "R1")
    assert code != 0, err
    assert "not both" in err


def test_research_add_refuses_a_stray_positional_id(repo):
    """`research add R1 --question q --verdict THEORETICAL` is not `research add --id R1`:
    the positional id belongs to `research show`, so a stray token is refused, not dropped."""
    run_cli(repo, "init")
    code, _, err = run_cli(
        repo, "research", "add", "R1", "--question", "q one", "--verdict", "THEORETICAL"
    )
    assert code != 0, err
    assert "research show" in err
