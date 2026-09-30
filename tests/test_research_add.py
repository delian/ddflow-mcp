"""`ddflow research add` runs, as the research gate's own instruction says it does.

The gate text (`services/gates.py`) tells every agent to record its verdict with
`ddflow research add --verdict ...` -- the spelling of `lesson add`, `decision add` and
`memory add` -- and the CLI answered "unrecognized arguments: add". Both spellings work.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _record(repo: Path, *head: str) -> dict:
    run_cli(repo, "init")
    code, out, err = run_cli(
        repo,
        "--json",
        *head,
        "--question",
        "does it parse?",
        "--verdict",
        "CONFIRMED",
        "--probe",
        "ddflow research add --help",
    )
    assert code == 0, err
    return json.loads(out)


def test_research_add_records_a_verdict(repo):
    _record(repo, "research", "add")


def test_the_bare_form_still_works(repo):
    _record(repo, "research")


def test_the_research_gate_instruction_names_a_command_that_parses():
    from ddflow.services import gates as G
    from ddflow.surfaces.cli import build_parser

    text = Path(G.__file__).read_text("utf-8")
    cmd = re.search(r"`(ddflow research add[^`]*)`", text)
    assert cmd, "the research gate no longer names its command"
    argv = cmd.group(1).split()[1:]
    argv = [a for a in argv if "|" not in a] + ["CONFIRMED", "--question", "q", "--probe", "p"]
    build_parser().parse_args(argv)
