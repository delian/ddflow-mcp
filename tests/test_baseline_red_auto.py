"""A measured baseline-red partial is accepted without a person (D-baseline-red-auto-accept).

The fixture is `test_gate_baseline_red`'s: a fake pytest that fails ``test_a`` on the base too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_gate_baseline_red import ID, _project

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.core.records import GateRecord
from ddflow.infra.log import EventLog
from ddflow.services import gate_accept as GA
from ddflow.services import inbox as IB
from ddflow.services import testscope as TSC
from ddflow.services.gates import baseline as BL
from ddflow.services.gates import baseline_red as BR
from ddflow.services.gates.defs import GateDef


def _state(repo):
    return fold(EventLog(repo).read_all(), strict=False)


def _cfg(repo):
    return Config.load(repo)


def _run(repo):
    _code, out, _e = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests", agent="worker")
    return json.loads(out)


def test_a_subset_of_the_baseline_failures_is_accepted_and_told_once(repo) -> None:
    _project(repo)
    body = _run(repo)
    assert body["outcome"] == "partial"
    st = _state(repo)
    it = st.items["T1"]
    auto = it.gates["unit_tests"].evidence["baseline"]["auto"]
    assert auto["accepted"] is True and auto["ids"] == [ID]
    got = GA.accepted(st, it, "unit_tests", _cfg(repo))
    assert got.ok and "accepted-baseline-red" in got.reason
    assert it.gate_outcome("unit_tests") == "partial"  # never passed
    assert not GA.accepted(st, it, "unit_tests").ok  # no cfg: only a person
    bug = st.bugs[it.gates["unit_tests"].evidence["baseline"]["bug"]]
    assert "baseline-red" in (getattr(bug, "tags", None) or [])
    notices = [r for r in __import__("ddflow.core.handlers.asks", fromlist=["x"]).asks_of(st).values()
               if r["answered_by"] == "policy:baseline_red_auto"]  # fmt: skip
    assert len(notices) == 1 and ID in notices[0]["text"]
    msgs = IB.pending(repo, "coordinator")
    assert len(msgs) == 1 and ID in msgs[0].text
    bugs = set(_state(repo).bugs)
    _run(repo)  # the same measurement again files and says nothing more
    again = _state(repo)
    assert set(again.bugs) == bugs
    assert len(IB.pending(repo, "coordinator")) == 1


def test_the_knob_off_leaves_it_to_a_person(repo) -> None:
    _project(repo)
    run_cli(repo, "config", "gates.baseline_red_auto", "off")
    assert _run(repo)["outcome"] == "partial"
    st = _state(repo)
    assert not GA.accepted(st, st.items["T1"], "unit_tests", _cfg(repo)).ok
    assert IB.pending(repo, "coordinator") == []


def test_a_failure_in_a_file_the_item_changed_is_not_accepted(repo) -> None:
    _project(repo)
    (repo / "tests" / "test_x.py").write_text("def test_a():\n    assert True\n")
    assert _run(repo)["outcome"] == "partial"
    st = _state(repo)
    auto = st.items["T1"].gates["unit_tests"].evidence["baseline"]["auto"]
    assert auto["accepted"] is False and "tests/test_x.py" in auto["why"]
    assert not GA.accepted(st, st.items["T1"], "unit_tests", _cfg(repo)).ok


def test_the_base_is_run_once_per_sha_and_shared(repo) -> None:
    _project(repo)
    calls: list[str] = []

    def run(gdef, cwd):
        calls.append(gdef.command)
        return "failed", {"failed_tests": [ID]}

    gdef = GateDef(id="unit_tests", command="pytest -q tests/")
    a = BR.on_base(repo, "main", gdef, [ID], run, 3600)
    b = BR.on_base(repo, "main", gdef, [ID], run, 3600)
    assert a["red"] and b["red"] and b.get("cached") and len(calls) == 1
    assert (
        BR.on_base(repo, "main", gdef, [ID], run, 0)["red"] and len(calls) == 2
    )  # ttl 0: no cache


def test_a_run_that_could_not_run_is_not_cached_and_never_red(repo) -> None:
    _project(repo)
    gdef = GateDef(id="unit_tests", command="pytest -q tests/")
    got = BR.on_base(repo, "main", gdef, [ID], lambda g, c: ("unavailable", {}), 3600)
    assert got["red"] is False
    sha = got["base_sha"]
    assert BL.cache_load(repo, sha, 3600) == {}


def test_an_unavailable_or_failed_gate_is_never_accepted() -> None:
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_red_auto="on"))
    for outcome in ("unavailable", "failed", "passed"):
        ev = {"baseline": {"red": True, "auto": {"accepted": True, "ids": [ID]}}}
        rec = GateRecord("unit_tests", outcome, "t", "a", "r", ev)
        it = SimpleNamespace(
            id="T1", gates={"unit_tests": rec}, gate_outcome=lambda g, o=outcome: o
        )
        assert GA.auto_ids(it, "unit_tests", cfg) == []


def test_ci_partial_baseline_red_does_not_stop_unit_tests() -> None:
    ev = {"baseline": {"red": True}}
    ci = GateRecord("ci", "partial", "t", "a", "baseline-red", ev)
    assert TSC._ci_baseline_red(SimpleNamespace(gates={"ci": ci}))
    ci = GateRecord("ci", "partial", "t", "a", "partial", {})
    assert not TSC._ci_baseline_red(SimpleNamespace(gates={"ci": ci}))
    assert not TSC._ci_baseline_red(SimpleNamespace(gates={}))


def test_an_open_bug_naming_the_ids_is_linked_not_refiled(repo) -> None:
    from ddflow.api import gate_baseline as GB

    _project(repo)
    run_cli(repo, "bug", "found", "--summary", f"{ID} fails on main", agent="worker")
    bug = next(iter(_state(repo).bugs))
    assert GB._linked(repo, "worker", [ID]) == bug
    assert GB._linked(repo, "worker", ["tests/other.py::t"]) == ""
    assert GB._linked(repo, "worker", [ID[:-1]]) == ""  # the start of a longer id is not the id
