"""Finished jobs are collected in bulk, and when their item completes or is abandoned
(B5127aa5436, B7b11533045).

`job end` took one id, so 112 exited jobs of finished items sat unended for days and each had
to be ended by hand. `job end --all-finished` / `--item X` end every EXITED or GONE job (never a
running one, never one on another host), and `complete` / `abandon` collect the item's own.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _wait(pred, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def _rows(repo: Path, *extra: str) -> list[dict]:
    code, out, err = run_cli(repo, "--json", "job", "list", *extra)
    if code == NOTHING:  # "no jobs": the list is empty, said in words
        return []
    assert code == OK, err
    rows = json.loads(out)
    assert isinstance(rows, list), out
    return rows


@pytest.fixture
def live(repo):
    """Start a job that stays running for the whole test, and is killed when it ends."""
    pids: list[int] = []

    def start(item: str) -> dict:
        job = _job(repo, item, "sleep 600")
        pids.append(job["pid"])
        return job

    yield start
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _task(repo: Path, name: str) -> None:
    run_cli(repo, "task", "add", name, "--globs", f"{name}.py")
    code, _o, err = run_cli(repo, "claim", name, "--no-worktree")
    assert code == OK, err


def _job(repo: Path, item: str, command: str) -> dict:
    code, out, err = run_cli(repo, "--json", "job", "run", item, command)
    assert code == OK, err
    return json.loads(out)


def _finished(repo: Path, *ids: str) -> bool:
    status = {r["id"]: r["status"] for r in _rows(repo)}
    return all(status.get(i) in ("exited", "gone") for i in ids)


def test_all_finished_ends_exited_jobs_and_leaves_a_running_one(repo, live):
    run_cli(repo, "init")
    _task(repo, "A")
    _task(repo, "B")
    one, two = _job(repo, "A", "exit 0"), _job(repo, "B", "exit 4")
    running = live("A")
    assert _wait(lambda: _finished(repo, one["id"], two["id"]))
    code, out, err = run_cli(repo, "--json", "job", "end", "--all-finished")
    assert code == OK, err
    assert sorted(json.loads(out)["ended"]) == sorted([one["id"], two["id"]])
    assert [r["id"] for r in _rows(repo)] == [running["id"]]
    ended = {r["id"]: r for r in _rows(repo, "--all") if r["status"] == "ended"}
    assert ended[two["id"]]["exit_code"] == 4


def test_item_ends_only_that_items_finished_jobs(repo):
    run_cli(repo, "init")
    _task(repo, "A")
    _task(repo, "B")
    a, b = _job(repo, "A", "exit 0"), _job(repo, "B", "exit 0")
    assert _wait(lambda: _finished(repo, a["id"], b["id"]))
    code, out, err = run_cli(repo, "--json", "job", "end", "--item", "A")
    assert code == OK, err
    assert json.loads(out)["ended"] == [a["id"]]
    assert [r["id"] for r in _rows(repo)] == [b["id"]]


def test_bulk_end_with_nothing_finished_is_nothing_to_do(repo, live):
    run_cli(repo, "init")
    _task(repo, "A")
    live("A")
    code, out, err = run_cli(repo, "job", "end", "--all-finished")
    assert code == NOTHING and "no finished jobs" in out + err


def test_a_job_id_and_a_bulk_flag_are_refused_together_and_one_is_needed(repo):
    run_cli(repo, "init")
    _task(repo, "A")
    done = _job(repo, "A", "exit 0")
    assert _wait(lambda: _finished(repo, done["id"]))
    code, _o, _e = run_cli(repo, "job", "end")
    assert code == FAIL
    # a REAL finished job: only the conflict itself can refuse this
    code, _o, _e = run_cli(repo, "job", "end", done["id"], "--all-finished")
    assert code == REFUSED
    assert [r["id"] for r in _rows(repo)] == [done["id"]], "the refused call ended it anyway"


def test_completing_an_item_collects_its_finished_jobs(repo):
    run_cli(repo, "init")
    _task(repo, "A")
    j = _job(repo, "A", "exit 0")
    assert _wait(lambda: _finished(repo, j["id"]))
    code, _o, err = run_cli(repo, "complete", "A", "--force")
    assert code == OK, err
    assert _rows(repo) == [], "a finished job of a completed item is still unended"


def test_abandoning_an_item_collects_its_finished_jobs_but_not_a_running_one(repo, live):
    run_cli(repo, "init")
    _task(repo, "A")
    done, running = _job(repo, "A", "exit 0"), live("A")
    assert _wait(lambda: _finished(repo, done["id"]))
    code, _o, err = run_cli(repo, "abandon", "A", "--reason", "x")
    assert code == OK, err
    assert [r["id"] for r in _rows(repo)] == [running["id"]]


def test_collect_finished_orders_jobs_even_without_a_start_time():
    from ddflow.api import jobs as AJ
    from ddflow.core.model import Job, State
    from ddflow.services import jobs as J

    class Log:
        def __init__(self):
            self.written = []

        def append_many(self, items):
            self.written.extend(items)

    st = State()
    for jid, started in (("J2", "2026-01-01T00:00:00Z"), ("J1", "")):  # inserted out of order
        st.jobs[jid] = Job(id=jid, item="A", pid=0, host=J.host(), started_at=started)
    log = Log()
    assert AJ.collect_finished(log, st) == ["J1", "J2"]
    assert [kind for kind, _s, _d in log.written] == ["job.ended", "job.ended"]


def test_a_job_on_another_host_is_never_collected():
    from ddflow.api import jobs as AJ
    from ddflow.core.model import Job, State

    class Log:
        def __init__(self):
            self.written = []

        def append_many(self, items):
            self.written.extend(items)

    st = State()
    st.jobs["F1"] = Job(id="F1", item="A", pid=0, host="some-other-host", started_at="2026-01-01")
    log = Log()
    assert AJ.collect_finished(log, st) == []
    assert not log.written


def test_item_and_all_finished_together_are_refused(repo):
    run_cli(repo, "init")
    code, _o, _e = run_cli(repo, "job", "end", "--item", "A", "--all-finished")
    assert code == REFUSED


def test_a_collection_that_cannot_run_is_reported_not_silent():
    from ddflow.api import jobs as AJ

    class Log:
        def read_all(self):
            raise OSError("log unreadable")

    ended, why = AJ.collect_for_item(Log(), "A")
    assert ended == [] and "not collected" in why and "log unreadable" in why


def test_bulk_end_refuses_a_single_jobs_exit_code_and_note(repo):
    run_cli(repo, "init")
    code, _o, _e = run_cli(repo, "job", "end", "--all-finished", "--exit-code", "42")
    assert code == REFUSED
    code, _o, _e = run_cli(repo, "job", "end", "--item", "A", "--note", "cancelled")
    assert code == REFUSED
    code, _o, _e = run_cli(repo, "job", "end", "--all-finished", "--force")
    assert code == REFUSED
