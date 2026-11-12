"""The batch record (BG-record, D-BG-3): `batch.*` events, a fold that replay rebuilds, and one
all-or-nothing fan-out of a batch gate run to every member's own gate outcome."""

from __future__ import annotations

import pytest

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.services import batchgate as BG

MEMBERS = [
    {"item": "A", "tip": "a" * 40, "paths": ["a/**"]},
    {"item": "B", "tip": "b" * 40, "paths": ["b/x.py"]},
    {"item": "C", "tip": "c" * 40, "paths": ["c/**"]},
]


def _st(log):
    return fold(log.read_all(), strict=False)


def _outcomes(log, gate="ci"):
    return [e for e in log.read_all() if e.kind.startswith("gate.") and e.data.get("gate") == gate]


@pytest.fixture
def formed(log):
    BG.form(log, "b1", MEMBERS, trigger="count", base_sha="0" * 40)
    return log


def _start(log, tree="t", gate="ci"):
    BG.gate_started(log, "b1", gate=gate, tree_sha=tree, command="pytest")


def test_formed_batch_folds_with_members_tips_and_paths(formed):
    b = _st(formed).batches["b1"]
    assert b["state"] == "formed"
    assert [m["item"] for m in b["members"]] == ["A", "B", "C"]
    assert b["members"][1]["tip"] == "b" * 40
    assert b["members"][1]["paths"] == ["b/x.py"]
    assert b["trigger"] == "count" and b["base"] == "0" * 40


def test_replay_rebuilds_the_batch_exactly(formed):
    BG.gate_started(formed, "b1", gate="ci", tree_sha="t1", command="pytest -q")
    BG.fan_out(formed, Config.load(), "b1", gate="ci", outcome="passed", tree_sha="t1", by="q")
    BG.culprit(formed, "b1", member="B", runs=3)
    BG.landed(formed, "b1", sha="f" * 40)
    first = _st(formed).batches
    assert fold(formed.read_all(), strict=False).batches == first
    assert first["b1"]["state"] == "landed"
    assert first["b1"]["culprits"] == [{"member": "B", "runs": 3}]


def test_fan_out_gives_every_member_its_own_outcome_with_batch_evidence(formed):
    _start(formed, "tree9")
    BG.fan_out(
        formed,
        Config.load(),
        "b1",
        gate="ci",
        outcome="passed",
        tree_sha="tree9",
        by="queue",
        elapsed_s=12.5,
        queue_wait_s=3.0,
    )
    got = _outcomes(formed)
    assert sorted(e.subject for e in got) == ["A", "B", "C"]
    for e in got:
        assert e.kind == "gate.passed"
        ev = e.data["evidence"]
        assert ev["covered_by"] == "batch"
        assert ev["batch"] == "b1"
        assert ev["tree_sha"] == "tree9"
        assert ev["members"] == ["A", "B", "C"]
        assert ev["batch_gate_run"]
    run = _st(formed).batches["b1"]["gates"][0]
    assert run["outcome"] == "passed" and run["elapsed_s"] == 12.5 and run["queue_wait_s"] == 3.0


def test_a_gate_that_did_not_run_is_unavailable_never_passed(formed):
    with pytest.raises(ValueError, match="reason"):
        BG.fan_out(formed, Config.load(), "b1", gate="ci", outcome="unavailable", tree_sha="t")
    BG.fan_out(
        formed,
        Config.load(),
        "b1",
        gate="ci",
        outcome="unavailable",
        tree_sha="t",
        reason="suite lock wait expired",
    )
    assert {e.kind for e in _outcomes(formed)} == {"gate.unavailable"}


def test_a_culprit_member_fails_and_the_rest_keep_the_batch_outcome(formed):
    _start(formed)
    BG.fan_out(
        formed,
        Config.load(),
        "b1",
        gate="ci",
        outcome="passed",
        tree_sha="t",
        member_outcomes={"B": ("failed", "its test broke")},
    )
    kinds = {e.subject: e.kind for e in _outcomes(formed)}
    assert kinds == {"A": "gate.passed", "B": "gate.failed", "C": "gate.passed"}
    failed = next(e for e in _outcomes(formed) if e.subject == "B")
    assert failed.data["reason"] == "its test broke"


def test_fan_out_is_all_or_nothing(formed):
    """A member outcome that cannot be recorded stops the whole fan-out: no member and no
    batch event is written."""
    _start(formed)
    before = len(formed.read_all())
    with pytest.raises(ValueError):
        BG.fan_out(
            formed,
            Config.load(),
            "b1",
            gate="ci",
            outcome="passed",
            tree_sha="t",
            member_outcomes={"C": ("failed", "")},  # a failure with no reason
        )
    assert len(formed.read_all()) == before


def test_a_member_outside_the_batch_is_refused(formed):
    _start(formed)
    with pytest.raises(ValueError, match="not a member"):
        BG.fan_out(
            formed,
            Config.load(),
            "b1",
            gate="ci",
            outcome="passed",
            tree_sha="t",
            member_outcomes={"Z": ("failed", "x")},
        )


def test_unknown_or_closed_batches_are_refused(formed):
    with pytest.raises(ValueError, match="no such batch"):
        BG.fan_out(formed, Config.load(), "nope", gate="ci", outcome="passed", tree_sha="t")
    BG.dissolved(formed, "b1", reason="flake")
    assert _st(formed).batches["b1"]["state"] == "dissolved"
    assert _st(formed).batches["b1"]["reason"] == "flake"
    with pytest.raises(ValueError, match="already dissolved"):
        BG.fan_out(formed, Config.load(), "b1", gate="ci", outcome="passed", tree_sha="t")
    with pytest.raises(ValueError, match="already dissolved"):
        BG.landed(formed, "b1", sha="f" * 40)


def test_a_batch_id_is_formed_once(formed):
    with pytest.raises(ValueError, match="already"):
        BG.form(formed, "b1", MEMBERS, trigger="count", base_sha="0" * 40)


def test_a_batch_of_one_and_a_member_in_two_batches(log):
    BG.form(log, "solo", MEMBERS[:1], trigger="age", base_sha="0" * 40)
    assert [m["item"] for m in _st(log).batches["solo"]["members"]] == ["A"]
    with pytest.raises(ValueError, match="already in batch solo"):
        BG.form(log, "other", MEMBERS[:2], trigger="count", base_sha="0" * 40)
    with pytest.raises(ValueError, match="at least one member"):
        BG.form(log, "empty", [], trigger="count", base_sha="0" * 40)


def test_a_human_gate_is_never_cleared_by_a_batch(formed):
    from ddflow.services.gates.defs import GateDef

    gdef = GateDef(id="ci", human=True)
    before = len(formed.read_all())
    with pytest.raises(ValueError, match="human"):
        BG.fan_out(
            formed,
            Config.load(),
            "b1",
            gate="ci",
            outcome="passed",
            tree_sha="t",
            gates={"ci": gdef},
        )
    assert len(formed.read_all()) == before


def test_a_pass_needs_a_started_run_of_that_gate_and_tree(formed):
    before = len(formed.read_all())
    with pytest.raises(ValueError, match="never started"):
        BG.fan_out(formed, Config.load(), "b1", gate="ci", outcome="passed", tree_sha="t")
    _start(formed, "other-tree")
    with pytest.raises(ValueError, match="never started"):
        BG.fan_out(formed, Config.load(), "b1", gate="ci", outcome="passed", tree_sha="t")
    assert len(formed.read_all()) == before + 1  # only the start


def test_a_human_gate_from_the_project_is_refused_without_a_gates_argument(formed, repo):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text("[gate.ci]\nhuman = true\n")
    _start(formed)
    before = len(formed.read_all())
    with pytest.raises(ValueError, match="human"):
        BG.fan_out(formed, Config.load(repo), "b1", gate="ci", outcome="passed", tree_sha="t")
    assert len(formed.read_all()) == before


def test_a_member_needs_a_tip_and_a_path_manifest(log):
    with pytest.raises(ValueError, match="tip"):
        BG.form(log, "b", [{"item": "i1", "paths": ["a/**"]}], trigger="count", base_sha="0")
    with pytest.raises(ValueError, match="paths"):
        BG.form(log, "b", [{"item": "i1", "tip": "a" * 40}], trigger="count", base_sha="0")


def test_a_finish_without_an_outcome_still_closes_the_run(formed):
    run = BG.gate_started(formed, "b1", gate="ci", tree_sha="t")
    formed.append("batch.gate_finished", "b1", {"gate": "ci", "run": run})
    formed.append("batch.gate_finished", "b1", {"gate": "ci", "run": run, "outcome": "passed"})
    assert len(_st(formed).batches["b1"]["gates"]) == 2  # the second is its own entry
