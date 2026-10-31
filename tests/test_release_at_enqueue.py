"""Release at enqueue (LC-release): the agent leaves the item at the merge queue, holds no
lease while the integrator waits, and a red run parks the item and wakes its lane."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import append_config, run_cli
from test_merge_queue import _finish, _git, _landed, _status, proj  # noqa: F401

from ddflow.services import inbox as IB
from ddflow.services import mergequeue as MQ


@pytest.fixture
def rp(proj: Path) -> Path:  # noqa: F811
    err, _ = append_config(proj, '[agent]\nfamily = "anthropic"\n')
    assert not err
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "author family")
    return proj


def _release(p: Path, item: str, agent: str = ""):
    return run_cli(p, "--json", "queue", "add", item, "--release", agent=agent or item.lower())


def _item(p: Path, item: str) -> dict:
    code, out, err = run_cli(p, "--json", "show", item)
    assert code == 0, out + err
    return json.loads(out)


def _lease_of(p: Path, item: str):
    return _item(p, item).get("lease")


def test_a_release_ends_the_lease_and_the_integrator_completes_the_item(rp):
    _finish(rp, "T1")
    code, out, err = _release(rp, "T1")
    assert code == 0, out + err
    body = json.loads(out)
    assert body["released"] is True and body["queued"] is True
    assert not _lease_of(rp, "T1"), "the agent still holds a lease while the item waits"
    assert _status(rp)["T1"]["released"] is True
    told = IB.pending(rp, "t1")
    assert told and told[0].reason == "ready" and "claim your next item" in told[0].text
    code, out, err = run_cli(rp, "queue", "run", agent="integrator")
    assert code == 0, out + err
    assert _landed(rp) == ["T1"]
    assert _item(rp, "T1")["state"] == "done", "the item did not complete without its agent"


def test_release_is_refused_while_a_judgement_gate_is_open(rp):
    _finish(rp, "T1")
    r = run_cli(
        rp,
        "gate",
        "record",
        "T1",
        "critic",
        "--outcome",
        "unavailable",
        "--reason",
        "no reviewer",
        "--evidence",
        "x",
    )
    assert r[0] == 0, r
    code, out, err = _release(rp, "T1")
    assert code == 3 and "critic" in out + err, out + err
    assert MQ.get(rp, "T1") is None
    assert _lease_of(rp, "T1"), "a refused release must leave the lease alone"


def test_release_is_refused_when_the_tree_changed_after_a_judgement_gate(rp):
    tree = _finish(rp, "T1")
    (tree / "knobs" / "t1.txt").write_text("changed after the review\n")
    _git(tree, "commit", "-qam", "late edit")
    code, out, err = _release(rp, "T1")
    assert code == 3 and "tree changed" in out + err, out + err
    assert MQ.get(rp, "T1") is None and _lease_of(rp, "T1")


def test_an_agent_cannot_release_someone_elses_item(rp):
    _finish(rp, "T1")
    code, out, err = _release(rp, "T1", agent="mallory")
    assert code == 3 and "only its holder" in out + err, out + err
    assert MQ.get(rp, "T1") is None
    assert _lease_of(rp, "T1")["holder"] == "t1"


def test_no_lease_is_held_while_the_integrator_waits_for_the_suite(rp):
    _finish(rp, "T1")
    assert _release(rp, "T1")[0] == 0
    hold = rp.parent / "hold"
    hold.write_text("")  # ci blocks here until removed
    first = subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(rp), "--agent", "one", "queue", "run"],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(1200):
            if MQ.holder(rp):
                break
            time.sleep(0.05)
        assert MQ.holder(rp), "the integrator never took the lock"
        assert not _lease_of(rp, "T1"), "hold-and-wait: a lease is held during the suite"
    finally:
        hold.unlink(missing_ok=True)
        first.communicate(timeout=120)
    assert first.returncode == 0 and _item(rp, "T1")["state"] == "done"


def test_a_red_run_parks_the_item_with_a_resume_brief_and_wakes_the_lane(rp):
    _finish(rp, "T1", "BAD\n")
    assert _release(rp, "T1")[0] == 0
    code, out, err = run_cli(rp, "queue", "run", agent="integrator")
    assert code == 1, out + err
    row = _status(rp)["T1"]
    assert row["state"] == "parked" and row["released"] is True
    assert _item(rp, "T1")["state"] != "done", "a parked item must not be lost or finished"
    texts = [m.text for m in IB.pending(rp, "t1") if m.reason == "message"]
    assert texts and "PARKED" in texts[0] and "ddflow claim T1" in texts[0], texts


def test_two_released_items_do_not_both_take_the_last_slot(rp):
    err, _ = append_config(rp, "[lanes]\nrelease_max = 1\n")
    assert not err
    _git(rp, "add", "-A")
    _git(rp, "commit", "-qm", "one slot")
    _finish(rp, "T1")
    _finish(rp, "T2")
    assert _release(rp, "T1")[0] == 0
    code, out, err = _release(rp, "T2")
    assert code == 3 and "already wait" in out + err, out + err
    assert _lease_of(rp, "T2"), "a refused release must not drop the lease"
    assert MQ.get(rp, "T2") is None


def test_the_slot_count_is_taken_under_one_lock(tmp_path):
    won, lost = [], []

    def go(item: str) -> None:
        try:
            MQ.add(
                tmp_path, item, agent=item, keep_history=5, release={"author": "a"}, release_cap=1
            )
            won.append(item)
        except MQ.SlotsFull:
            lost.append(item)

    threads = [threading.Thread(target=go, args=(f"T{n}",)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(won) == 1 and len(lost) == 7, (won, lost)


def test_a_crashed_integrator_leaves_the_released_item_for_recover(rp):
    _finish(rp, "T1")
    assert _release(rp, "T1")[0] == 0
    entry = MQ.get(rp, "T1")
    entry.state, entry.started_at = MQ.RUNNING, time.time()
    MQ.save(rp, entry)  # an integrator died holding it
    code, out, err = run_cli(rp, "recover")
    assert code in (0, 1), out + err  # a plan, not a crash
    assert "T1" in out + err, out + err
    assert MQ.get(rp, "T1").released is True


def test_release_mode_off_refuses_a_release(rp):
    _finish(rp, "T1")
    err, _ = append_config(rp, '[lanes]\nrelease_mode = "off"\n', local=True)
    assert not err
    code, out, err = _release(rp, "T1")
    assert code == 3 and "release_mode" in out + err
    assert _lease_of(rp, "T1")


def test_auto_mode_releases_an_eligible_plain_enqueue_and_only_queues_the_rest(rp):
    err, _ = append_config(rp, '[lanes]\nrelease_mode = "auto"\n', local=True)
    assert not err
    _finish(rp, "T1")
    tree = _finish(rp, "T2")
    (tree / "knobs" / "t2.txt").write_text("late\n")
    _git(tree, "commit", "-qam", "late edit")
    assert run_cli(rp, "queue", "add", "T1", agent="t1")[0] == 0
    assert run_cli(rp, "queue", "add", "T2", agent="t2")[0] == 0
    assert MQ.get(rp, "T1").released and not _lease_of(rp, "T1")
    assert not MQ.get(rp, "T2").released and _lease_of(rp, "T2")


def test_a_release_of_an_entry_already_being_integrated_is_refused(tmp_path):
    e, _ = MQ.add(tmp_path, "T1", agent="t1", keep_history=5)
    e.state = MQ.RUNNING
    MQ.save(tmp_path, e)
    with pytest.raises(MQ.ReleaseRefused):
        MQ.add(tmp_path, "T1", agent="t1", keep_history=5, release={"author": "a"})


def test_ci_and_merge_are_never_extra_machine_gates():
    from types import SimpleNamespace

    from ddflow.services import lanerelease as LR

    cfg = SimpleNamespace(lanes=SimpleNamespace(release_machine_gates=["ci", "standards", "merge"]))
    assert LR.machine_gates(cfg) == ("standards",)


def test_a_running_entry_is_refused_as_running_even_when_the_slots_are_full(tmp_path):
    MQ.add(tmp_path, "T0", agent="t0", keep_history=5, release={"author": "a"}, release_cap=1)
    e, _ = MQ.add(tmp_path, "T1", agent="t1", keep_history=5)
    e.state = MQ.RUNNING
    MQ.save(tmp_path, e)
    with pytest.raises(MQ.ReleaseRefused) as got:
        MQ.add(tmp_path, "T1", agent="t1", keep_history=5, release={"author": "a"}, release_cap=1)
    assert not isinstance(got.value, MQ.SlotsFull)
