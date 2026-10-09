"""`resolve --refile-as` refusals survive the helper extraction: a failed Outcome is falsy,
so the refusal must be tested `is not None`, never by truthiness."""

from __future__ import annotations

from ddflow.infra.log import EventLog
from tests.conftest import run_cli
from tests.test_divergence import _show, rival_adds, two_clones  # noqa: F401


def test_refile_as_count_mismatch_is_refused_and_writes_nothing(rival_adds):  # noqa: F811
    b = rival_adds
    alice = next(d for d in _show(b, "T2")["contested"] if d["agent"] == "alice")
    before = len(EventLog(b, "probe").read_all())
    code, out, err = run_cli(
        b, "resolve", "T2", "--keep", alice["event"], "--refile-as", "x1,x2,x3", agent="bob"
    )
    assert code != 0
    assert "gives 3 id(s) for 1 losing definition(s)" in out + err
    assert len(EventLog(b, "probe").read_all()) == before
    assert _show(b, "T2")["contested"], "the contest is still open"


def test_refile_as_taken_id_is_refused(rival_adds):  # noqa: F811
    b = rival_adds
    alice = next(d for d in _show(b, "T2")["contested"] if d["agent"] == "alice")
    code, out, err = run_cli(
        b, "resolve", "T2", "--keep", alice["event"], "--refile-as", "T2", agent="bob"
    )
    assert code != 0, out + err
    assert _show(b, "T2")["contested"]
