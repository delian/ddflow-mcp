"""A merge queue is reported as one: enqueued, position, ejected (B172).

`gh pr merge` on a queue-protected branch ENQUEUES instead of merging. ddflow saw "not
merged yet" and kept the item in review -- correct, but it could not say the request was
queued, where it stood, or that the queue had ejected it.
"""

# ruff: noqa: F811  (fixtures imported from test_flow)
from __future__ import annotations

import json

from conftest import run_cli
from test_flow import AUTHOR, _state, _work, pr_repo  # noqa: F401

GREEN = [{"status": "COMPLETED", "conclusion": "SUCCESS"}]


def _approved_and_green(pr_repo):
    repo, forge, _remote = pr_repo
    forge.set(merge_queue=True)
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    forge.approve(1)
    forge.edit(1, checks=GREEN)
    return repo, forge


def _sync(repo) -> dict:
    code, out, err = run_cli(repo, "--json", "pr", "sync")
    assert code == 0, err
    return json.loads(out)


def _row(repo) -> dict:
    _, out, _ = run_cli(repo, "--json", "pr", "status")
    return next(r for r in json.loads(out)["rows"] if r["id"] == "T1")


def test_an_enqueued_request_is_reported_as_queued_with_its_position(pr_repo):
    repo, forge = _approved_and_green(pr_repo)
    body = _sync(repo)
    queued = [c for c in body["changes"] if c["what"] == "queued"]
    assert queued and "position 1" in queued[0]["detail"], body["changes"]
    assert body["waiting"][0]["queue_position"] == 1 and body["waiting"][0]["queued"] is True
    row = _row(repo)
    assert (row["queue"], row["queue_position"]) == ("queued", 1), row
    assert _state(repo).items["T1"].state == "review", "still the queue's to finish"


def test_a_queued_request_is_not_merged_again_or_re_reported(pr_repo):
    repo, forge = _approved_and_green(pr_repo)
    _sync(repo)
    again = _sync(repo)
    assert len(forge.calls("pr", "merge")) == 1, "asked the forge to merge a queued request twice"
    assert not [c for c in again["changes"] if c["what"] == "queued"], again["changes"]


def test_an_ejection_is_reported_as_one(pr_repo):
    repo, forge = _approved_and_green(pr_repo)
    _sync(repo)
    forge.queue_eject(1)
    body = _sync(repo)
    ejected = [c for c in body["changes"] if c["what"] == "queue_ejected"]
    assert ejected and "ejected" in ejected[0]["detail"], body["changes"]
    assert _row(repo)["queue"] == ""
    assert _state(repo).items["T1"].state == "review", "the request is still open"


def test_a_request_the_queue_landed_completes_the_item(pr_repo):
    repo, forge = _approved_and_green(pr_repo)
    _sync(repo)
    forge.queue_land(1)
    body = _sync(repo)
    whats = [c["what"] for c in body["changes"]]
    assert "merged" in whats and "completed" in whats, whats
    assert _state(repo).items["T1"].state == "done"
