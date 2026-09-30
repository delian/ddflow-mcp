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


def _recorded(repo: Path, rid: str) -> str:
    code, out, err = run_cli(repo, "recall", "does it parse", "--max-chars", "4000")
    assert code == 0, err
    return out


def test_research_add_records_a_verdict(repo):
    out = _record(repo, "research", "add")
    assert out.get("verdict") == "CONFIRMED" and out.get("id"), out
    assert out["id"] in _recorded(repo, out["id"])


def test_the_bare_form_still_works(repo):
    out = _record(repo, "research")
    assert out.get("verdict") == "CONFIRMED" and out.get("id"), out


def test_help_shows_the_add_spelling(repo):
    code, out, _ = run_cli(repo, "research", "--help")
    assert code == 0 and "research add" in out


def test_the_research_gate_instruction_names_a_command_that_parses():
    from ddflow.services import gates as G
    from ddflow.surfaces.cli import build_parser

    shown = G.DEFAULT_GATES["research"].prompt
    cmd = re.search(r"`(ddflow research add[^`]*)`", shown)
    assert cmd, f"the research gate no longer names its command: {shown[:200]}"
    # `A|B|C` placeholders take their first alternative, in place.
    argv = [a.split("|")[0] for a in cmd.group(1).split()[1:]]
    build_parser().parse_args([*argv, "--question", "q", "--probe", "p"])
