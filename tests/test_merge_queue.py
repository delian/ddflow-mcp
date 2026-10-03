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
    repo, _forge = _approved_and_green(pr_repo)
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


def test_a_queue_that_could_not_be_asked_is_not_an_ejection(pr_repo):
    """A rate limit on the GraphQL call is "do not know": the request is still queued, and
    must neither be reported ejected nor be asked to merge again."""
    repo, forge = _approved_and_green(pr_repo)
    _sync(repo)
    forge.set(graphql_down=True)
    body = _sync(repo)
    assert not [c for c in body["changes"] if c["what"] == "queue_ejected"], body["changes"]
    assert len(forge.calls("pr", "merge")) == 1, "re-merged a request that is still queued"
    assert _row(repo)["queue"] == "queued", "the last known queue state must be kept"


def test_positions_move_up_as_the_queue_drains(pr_repo):
    repo, forge, _ = pr_repo
    forge.set(merge_queue=True)
    for n, name in enumerate(("a.py", "b.py"), start=1):
        _work(repo, f"T{n}", name, f"{n}\n")
        run_cli(repo, "merge", f"T{n}", "--model", AUTHOR)
        forge.approve(n)
        forge.edit(n, checks=GREEN)
    _sync(repo)
    rows = {r["id"]: r for r in json.loads(run_cli(repo, "--json", "pr", "status")[1])["rows"]}
    assert (rows["T1"]["queue_position"], rows["T2"]["queue_position"]) == (1, 2)
    forge.queue_land(1)
    _sync(repo)
    rows = {r["id"]: r for r in json.loads(run_cli(repo, "--json", "pr", "status")[1])["rows"]}
    assert rows["T2"]["queue_position"] == 1, rows["T2"]


def test_pr_status_text_shows_the_queue(pr_repo):
    repo, _forge = _approved_and_green(pr_repo)
    _sync(repo)
    _, out, _ = run_cli(repo, "pr", "status")
    assert "[merge queue #1]" in out, out


def test_a_queue_that_could_not_be_asked_keeps_its_state_when_something_else_changes(pr_repo):
    """The restored queue state must be what the `pr.synced` event records too, or the
    projection reads the next fold as an ejection (the event is built from the same info)."""
    repo, forge = _approved_and_green(pr_repo)
    _sync(repo)
    forge.set(graphql_down=True)
    forge.edit(1, checks=[{"status": "IN_PROGRESS", "conclusion": ""}])  # something else moved
    _sync(repo)
    row = _row(repo)
    assert (row["queue"], row["queue_position"]) == ("queued", 1), row
    assert row["checks"] == "pending", "the other change must still be recorded"


def test_a_queue_state_change_without_a_move_is_seen(pr_repo):
    repo, forge = _approved_and_green(pr_repo)
    _sync(repo)
    forge.edit(1, queue={"seq": 1, "state": "UNMERGEABLE"})
    _sync(repo)
    assert _row(repo)["queue_state"] == "UNMERGEABLE"


def test_an_unaskable_queue_never_triggers_a_merge(pr_repo, monkeypatch):
    """Approved and green, but the queue call hangs (ForgeUnavailable): the request may
    already be queued, so ddflow must not ask the forge to merge it."""
    from ddflow.infra import forge as FG

    repo, _forge = _approved_and_green(pr_repo)
    real = FG._run

    def hang_on_graphql(repo_, argv, **kw):
        if argv[:3] == ["gh", "api", "graphql"]:
            raise FG.ForgeUnavailable("gh api graphql timed out")
        return real(repo_, argv, **kw)

    monkeypatch.setattr(FG, "_run", hang_on_graphql)
    from ddflow import api

    out = api.pr_sync(repo)
    assert out.data["waiting"], out.data
    assert "merge queue could not be read" in out.data["waiting"][0]["note"], out.data["waiting"]
    assert not _forge.calls("pr", "merge"), "merged a request whose queue state is unknown"
