"""The executor's duplicate-check test reads ``candidates`` only when it is a list: bisect's
``candidates`` is a count, and its exit-2 report must reach stdout (B-uc-surf-setup-ops)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ddflow.core.outcome import NOTHING, OK, REFUSED
from ddflow.surfaces import cliexec
from ddflow.surfaces.cliexec import _asked_duplicates
from ddflow.surfaces.registry import CliPolicy


def _ctx():
    """What `_run_policy` reads in the body-always branch: `--json`."""
    return SimpleNamespace(json=True)


def _out(exit_code, candidates):
    return SimpleNamespace(exit=exit_code, data={"candidates": candidates})


def test_a_count_is_not_a_duplicate_answer():
    assert not _asked_duplicates(_out(2, 5))


def test_a_list_on_a_non_zero_exit_is_one():
    assert _asked_duplicates(_out(3, [{"id": "T1"}]))


def test_an_empty_list_or_a_zero_exit_is_not():
    assert not _asked_duplicates(_out(3, []))
    assert not _asked_duplicates(_out(0, [{"id": "T1"}]))


def _body_always_cmd():
    """The smallest command `_run_policy` reads: a policy that always prints the body."""
    return SimpleNamespace(cli=CliPolicy(body_always=True), payload=())


class _Out:
    """The smallest Outcome it reads: an exit, a reason, a body. A trivially-valid one,
    because what is under test is what gets printed, not what was decided."""

    def __init__(self, exit_code: int, reason: str = "") -> None:
        self.exit = exit_code
        self.reason = reason
        self.data: dict = {}

    def body(self, payload=()):
        return {"ok": self.exit == OK}


@pytest.mark.parametrize(
    ("exit_code", "reason"),
    [
        (NOTHING, ""),  # nothing to report: an empty reason is not a line
        (OK, "a note"),  # a success: its reason is not a stderr line either
    ],
)
def test_a_json_body_always_run_prints_stderr_only_for_a_refusal_with_a_reason(
    capsys, exit_code, reason
):
    """The `--json` body-always contract, one clause per argument.

    The mutant (`and` -> `or`) prints a bare newline when the exit is not OK and the reason
    is empty, and prints a SUCCESS's reason -- neither of which a caller reading stderr
    asked for, and both invisible until something checks stderr is exactly empty
    (mutation sample 2026-10-10: this line survived 40 test files).
    """
    code = cliexec._run_policy(_body_always_cmd(), {}, _Out(exit_code, reason), _ctx())

    assert code == exit_code
    assert capsys.readouterr().err == ""


def test_a_json_refusal_with_a_reason_does_print_it_on_stderr(capsys):
    """...and the clause it exists for is still there: the reason, on stderr, once."""
    code = cliexec._run_policy(_body_always_cmd(), {}, _Out(REFUSED, "no"), _ctx())

    assert code == REFUSED
    assert capsys.readouterr().err == "no\n"
