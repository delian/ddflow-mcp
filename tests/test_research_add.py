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

import pytest

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


@pytest.mark.parametrize("head", [("research", "add"), ("research",)])
def test_both_spellings_record_a_finding_that_recall_finds(repo, head):
    out = _record(repo, *head)
    assert out.get("verdict") == "CONFIRMED" and out.get("id"), out
    code, found, err = run_cli(repo, "recall", "does it parse", "--max-chars", "4000")
    assert code == 0 and out["id"] in found, err or found


def test_help_shows_the_add_spelling(repo):
    code, out, _ = run_cli(repo, "research", "--help")
    assert code == 0 and "{add,list}" in out


def test_the_research_gate_instruction_names_a_command_that_parses():
    from ddflow.services import gates as G
    from ddflow.surfaces.cli import build_parser

    shown = G.DEFAULT_GATES["research"].prompt
    cmd = re.search(r"`(ddflow research add[^`]*)`", shown)
    assert cmd, f"the research gate no longer names its command: {shown[:200]}"
    # `A|B|C` placeholders take their first alternative, in place.
    argv = [a.split("|")[0] for a in cmd.group(1).split()[1:]]
    build_parser().parse_args([*argv, "--question", "q", "--probe", "p"])


@pytest.mark.parametrize("verdict", ["CONFIRMED", "REFUTED"])
def test_a_probe_output_without_the_probe_is_refused(repo, verdict):
    """Bug Bc9802f9d9d: the guard accepted --probe-output alone, though the refusal, the
    driver and the README all say CONFIRMED and REFUTED require a --probe -- an output
    with no command behind it cannot be re-run."""
    run_cli(repo, "init")
    code, _, err = run_cli(
        repo,
        "research",
        "--question",
        "is it fast?",
        "--verdict",
        verdict,
        "--probe-output",
        "it was fast",
    )
    assert code == 1 and "requires a --probe" in err, (code, err)
    code, _, err = run_cli(
        repo, "research", "--question", "q1", "--verdict", verdict, "--probe", "   "
    )
    assert code == 1 and "requires a --probe" in err, (code, err)
    code, _, err = run_cli(
        repo, "research", "--question", "q2", "--verdict", "THEORETICAL", "--probe-output", "x"
    )
    assert code == 0, err
