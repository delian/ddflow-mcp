"""Be2de070703: `gate record --approval request` on a non-override outcome was silently ignored.

An agent recording a `partial` (an outcome it is entitled to record without any approval)
passed `--approval request`, and got back `{outcome: partial, warning: ""}` -- no Q id,
nothing filed, no word that the argument was dropped. The agent had nothing to hand over.
The argument must not vanish: recording such an outcome needs no override, so passing
`--approval` is refused, naming the record that needs no approval and the real route.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, REFUSED = 0, 1, 3


def _init(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p", "--globs", "src/**")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--phase", "P1", "--globs", "src/x.py")
    run_cli(repo, "claim", "T1")


def test_an_ignored_approval_on_a_partial_is_refused_not_silent(repo):
    _init(repo)
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "unit_tests",
        "--outcome",
        "partial",
        "--reason",
        "ran only some tests",
        "--approval",
        "request",
    )
    assert code == REFUSED, (code, out, err)
    assert "no operator's approval" in out + err, (out, err)


def test_a_real_approval_id_on_a_partial_is_refused_too(repo):
    _init(repo)
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "unit_tests",
        "--outcome",
        "partial",
        "--reason",
        "ran only some tests",
        "--approval",
        "Q0000000000000000000000000000000000000000",
    )
    assert code == REFUSED, (code, out, err)
    assert "nothing was filed or spent" in out + err, (out, err)


def test_a_partial_record_without_approval_still_succeeds(repo):
    _init(repo)
    code, _out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "unit_tests",
        "--outcome",
        "partial",
        "--reason",
        "ran only some tests",
    )
    assert code == OK, err
