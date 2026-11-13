"""Heartbeat leftovers (LK-auto-heartbeat-doctor):

- `heartbeat` on a completed, abandoned or released-open item is a successful no-op: it
  renews nothing and appends no lease event (the old code answered exit 2 "no lease held",
  and a stale live lease record got extended).
- `doctor` lists heartbeat-looking processes whose item is finished (pid and command
  line) so the operator can end a loop that outlived its item.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.infra.log import EventLog
from ddflow.services import orphan_suites as OS


def _git(*a: str) -> None:
    subprocess.run(["git", *a], check=True, capture_output=True)


def _claimed(repo: Path, item: str = "T1"):
    run_cli(repo, "init")
    seed = EventLog(repo, "seed")
    seed.append("phase.added", "P1", {"title": "p"})
    seed.append("task.added", item, {"parent": "P1", "title": item, "globs": ["a.py"]})
    out = LC.claim(repo, item, agent="worker")
    assert out.exit == 0, out.reason
    wt = Path(out.data["worktree"])
    (wt / "a.py").write_text("x = 1\n")
    _git("-C", str(wt), "add", "-A")
    _git("-C", str(wt), "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "w")
    return wt


def test_heartbeat_on_a_completed_item_is_a_successful_no_op(repo):
    """Fails on the unfixed code: exit 2, 'no lease held'."""
    _claimed(repo)
    EventLog(repo, "worker").append("item.completed", "T1", {})
    n = len(EventLog(repo, "worker").read_all())
    out = LC.heartbeat(repo, "T1", agent="worker")
    assert out.exit == 0, out.reason
    assert out.data["renewed"] is False
    assert out.data["note"] == "item is completed; nothing to renew"
    assert len(EventLog(repo, "worker").read_all()) == n  # no lease.renewed appended


def test_heartbeat_on_a_live_item_still_renews(repo):
    _claimed(repo)
    assert LC.heartbeat(repo, "T1", agent="worker").data["renewed"] is True


def test_heartbeat_on_a_released_open_item_is_a_successful_no_op(repo):
    """Fails on the unfixed code: exit 2, 'no lease held'."""
    _claimed(repo)
    assert LC.release(repo, "T1", agent="worker").exit == 0
    n = len(EventLog(repo, "worker").read_all())
    out = LC.heartbeat(repo, "T1", agent="worker")
    assert out.exit == 0, out.reason
    assert out.data["renewed"] is False
    assert out.data["note"] == "item has no lease (released); nothing to renew"
    assert len(EventLog(repo, "worker").read_all()) == n


def test_the_no_op_says_why_on_the_cli(repo):
    """Fails on the unfixed code: the note was not printed."""
    from ddflow.surfaces.declared import lifecycle_cli as L

    _claimed(repo)
    EventLog(repo, "worker").append("item.completed", "T1", {})
    out = LC.heartbeat(repo, "T1", agent="worker")
    assert L.heartbeat_text(out, {"id": "T1"}).startswith("item is completed; nothing to renew")


def test_heartbeat_on_an_item_never_held_still_says_no_lease_held(repo):
    """'No lease' is not 'released': an item this agent never held keeps exit 2."""
    run_cli(repo, "init")
    seed = EventLog(repo, "seed")
    seed.append("phase.added", "P1", {"title": "p"})
    seed.append("task.added", "T9", {"parent": "P1", "title": "t", "globs": ["z.py"]})
    out = LC.heartbeat(repo, "T9", agent="worker")
    assert out.exit == 2 and "no lease held" in out.reason


def test_the_newest_lease_event_decides_what_released_means(repo):
    """claim, release, claim again: the newest lease event is not a release."""
    from ddflow.api.lifecycle.heartbeat import _last_lease_event

    _claimed(repo)
    assert LC.release(repo, "T1", agent="worker").exit == 0
    assert _last_lease_event(EventLog(repo, "worker"), "T1") == "lease.released"
    assert LC.claim(repo, "T1", agent="worker", no_worktree=True).exit == 0
    assert _last_lease_event(EventLog(repo, "worker"), "T1") != "lease.released"


def _proc(root: Path, pid: int, *argv: str) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")


def test_a_loop_heartbeating_a_finished_item_is_listed_with_its_pid(tmp_path):
    _proc(
        tmp_path, 4001, "sh", "-c", "while true; do ddflow --agent a heartbeat T1; sleep 60; done"
    )
    _proc(tmp_path, 4002, "sh", "-c", "while true; do ddflow heartbeat T2; sleep 60; done")
    _proc(tmp_path, 4003, "vim", "notes-heartbeat T1")
    found = OS.stale_heartbeats(["T1"], tmp_path, uid=os.getuid())
    assert [(p, i) for p, i, _ in found] == [(4001, "T1")]
    note = OS.heartbeat_notes(found)[0]
    assert "pid 4001" in note and "kill 4001" in note


def test_no_finished_items_lists_nothing(tmp_path):
    _proc(tmp_path, 4001, "sh", "-c", "ddflow heartbeat T1")
    assert OS.stale_heartbeats([], tmp_path) == []


def test_a_valued_option_before_the_item_is_found(tmp_path):
    _proc(
        tmp_path,
        4001,
        "sh",
        "-c",
        "while true; do ddflow heartbeat --agent worker T1; sleep 9; done",
    )
    found = OS.stale_heartbeats(["T1"], tmp_path, uid=os.getuid())
    assert [(p, i) for p, i, _ in found] == [(4001, "T1")]


def test_a_quoted_option_value_with_spaces_does_not_hide_the_item(tmp_path):
    _proc(
        tmp_path,
        4001,
        "sh",
        "-c",
        'while true; do ddflow heartbeat --reason "still working" T1; sleep 60; done',
    )
    found = OS.stale_heartbeats(["T1"], tmp_path, uid=os.getuid())
    assert [(p, i) for p, i, _ in found] == [(4001, "T1")]


def test_a_command_that_only_mentions_heartbeat_is_not_one(tmp_path):
    _proc(tmp_path, 4001, "ddflow", "task", "comment", "--body", "heartbeat", "T1")
    assert OS.stale_heartbeats(["T1"], tmp_path, uid=os.getuid()) == []


def test_a_valued_option_value_with_a_space_before_the_item_is_the_value(tmp_path):
    """`--repo "/my repo"` is one argv token: it is the value, never the item scan."""
    _proc(tmp_path, 4001, "ddflow", "--repo", "/my repo", "heartbeat", "T1")
    found = OS.stale_heartbeats(["T1"], tmp_path, uid=os.getuid())
    assert [(p, i) for p, i, _ in found] == [(4001, "T1")]
