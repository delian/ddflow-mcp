"""Pins what the reporting and viewer commands print, byte for byte (B-uc-surf-reporting).

Recorded against the code before the slice moved: every command in `COMMANDS` is run on the
golden project in its human and its `--json` form, with the exit code and the two streams apart.
The slice changes where the code lives, not a byte of this.
"""

# ruff: noqa: F811 -- the goldenfix fixtures are imported, then named as parameters
from __future__ import annotations

import re

import pytest
from goldenfix import _pinned_environment, normalise, project  # noqa: F401 -- fixtures

from ddflow.surfaces.cli import main

COMMANDS = [
    ["status"],
    ["show", "T2"],
    ["show", "B1"],
    ["show", "T1"],
    ["show", "NOPE"],
    ["board"],
    ["board", "--phase", "P1"],
    ["board", "--phase", "nope"],
    ["recover"],
    ["rebuild"],
    ["render", "--show", "board"],
    ["render", "--show", "lessons"],
    ["render", "--show", "bogus"],
    ["replay"],
    ["replay", "--verify"],
    ["progress"],
    ["progress", "T1"],
    ["loops"],
    ["history"],
    ["task", "list"],
    ["task", "list", "--state", "done"],
    ["task", "list", "--phase", "P1", "--limit", "1"],
    ["task", "list", "--state", "bogus"],
    ["task", "list", "--owner", "nobody"],
    ["task", "list", "--tag", "x"],
    ["task", "list", "--since", "2020-01-01"],
    ["phase", "list"],
    ["phase", "list", "--state", "done"],
    ["research", "list", "--limit", "1"],
    ["bug", "list"],
    ["bug", "list", "--all"],
    ["bug", "list", "--item", "T1"],
    ["lesson", "list"],
    ["lesson", "list", "--all"],
    ["research", "list"],
    ["search", "tokenizer"],
    ["search", "--exact", "tok"],
    ["search", "--regex", "tok.*"],
    ["search", "--regex", "(a+)+$"],
    ["search", "zzzzqq"],
    ["session", "list"],
    ["session", "list", "--state", "open"],
    ["session", "show", "nope"],
]


@pytest.fixture
def ddflow(project, capsys):
    """One command in this process: (exit code, stdout, stderr), normalised -- the two
    streams apart, since which one a line goes to is part of the contract."""

    def run(*argv: str) -> tuple[int, str, str]:
        capsys.readouterr()
        code = main(["--repo", str(project), "--agent", "golden", *argv])
        cap = capsys.readouterr()
        out, err = (normalise(t, project, project.parent) for t in (cap.out, cap.err))
        return code, out, err

    return run


def _steady(result: tuple[int, str, str]) -> tuple[int, str, str]:
    """`rebuild` prints how long it took."""
    code, out, err = result
    return code, *(re.sub(r"in \d+\.\d\ds", "in <S>s", t) for t in (out, err))


def _id(argv: list[str]) -> str:
    return " ".join(argv)


@pytest.mark.parametrize("argv", COMMANDS, ids=_id)
def test_human(argv, ddflow, snapshot):
    assert _steady(ddflow(*argv)) == snapshot


@pytest.mark.parametrize("argv", COMMANDS, ids=_id)
def test_json(argv, ddflow, snapshot):
    assert _steady(ddflow("--json", *argv)) == snapshot


def test_a_list_flag_on_the_record_form_is_refused_by_the_parser(project, capsys):
    from ddflow.surfaces.cli import main

    with pytest.raises(SystemExit) as stop:
        main(["--repo", str(project), "research", "--state", "x"])
    assert stop.value.code == 2
    assert "apply to `research list`, not to recording a finding" in capsys.readouterr().err
