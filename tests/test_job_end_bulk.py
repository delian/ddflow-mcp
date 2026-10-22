"""Finished jobs are collected in bulk, and when their item completes or is abandoned
(B5127aa5436, B7b11533045).

`job end` took one id, so 112 exited jobs of finished items sat unended for days and each had
to be ended by hand. `job end --all-finished` / `--item X` end every EXITED or GONE job (never a
running one, never one on another host), and `complete` / `abandon` collect the item's own.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
    _c, out, _e = run_cli(repo, "--json", "job", "list", *extra)
    return json.loads(out) if out.strip().startswith("[") else []


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


def test_all_finished_ends_exited_jobs_and_leaves_a_running_one(repo):
    run_cli(repo, "init")
    _task(repo, "A")
    _task(repo, "B")
    one, two = _job(repo, "A", "exit 0"), _job(repo, "B", "exit 4")
    live = _job(repo, "A", "sleep 60")
    assert _wait(lambda: _finished(repo, one["id"], two["id"]))
    code, out, err = run_cli(repo, "--json", "job", "end", "--all-finished")
    assert code == OK, err
    assert sorted(json.loads(out)["ended"]) == sorted([one["id"], two["id"]])
    assert [r["id"] for r in _rows(repo)] == [live["id"]]
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


def test_bulk_end_with_nothing_finished_is_nothing_to_do(repo):
    run_cli(repo, "init")
    _task(repo, "A")
    _job(repo, "A", "sleep 60")
    code, out, err = run_cli(repo, "job", "end", "--all-finished")
    assert code == NOTHING and "no finished jobs" in out + err


def test_a_job_id_and_a_bulk_flag_are_refused_together_and_one_is_needed(repo):
    run_cli(repo, "init")
    code, _o, _e = run_cli(repo, "job", "end")
    assert code != OK
    code, _o, _e = run_cli(repo, "job", "end", "J1", "--all-finished")
    assert code != OK


def test_completing_an_item_collects_its_finished_jobs(repo):
    run_cli(repo, "init")
    _task(repo, "A")
    j = _job(repo, "A", "exit 0")
    assert _wait(lambda: _finished(repo, j["id"]))
    code, _o, err = run_cli(repo, "complete", "A", "--force")
    assert code == OK, err
    assert _rows(repo) == [], "a finished job of a completed item is still unended"


def test_abandoning_an_item_collects_its_finished_jobs_but_not_a_running_one(repo):
    run_cli(repo, "init")
    _task(repo, "A")
    done, live = _job(repo, "A", "exit 0"), _job(repo, "A", "sleep 60")
    assert _wait(lambda: _finished(repo, done["id"]))
    code, _o, err = run_cli(repo, "abandon", "A", "--reason", "x")
    assert code == OK, err
    assert [r["id"] for r in _rows(repo)] == [live["id"]]
