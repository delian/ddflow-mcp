"""B-rework-of-merged-item-keeps-stale-merge: a re-claim of an item that had already
merged (the operator changed the design) kept the old merge: `merged_sha` survived, the
merge gate still showed [x], and `gate run <id> ci` answered "merged, and the merge removed
its worktree" and recorded UNAVAILABLE over the new work. The re-claim reopens the stale
merge -- `merged_sha` and the merge/ci gates clear -- so the rework runs and re-merges in
the new tree."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import run_cli
from helpers import git as _git

from ddflow.core.model import fold
from ddflow.infra.log import EventLog


@pytest.fixture(autouse=True)
def _a_person_records_by_hand(monkeypatch):
    """These tests hand-record command gates as a person: an agent's typed pass is refused
    (Bed643a611b), and the suite itself may run under an agent harness."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)


def _merged(repo: Path, *, with_ci: bool = False, agent: str = "") -> str:
    """T1 claimed, one file committed on its branch, merged: `merged_sha` is set. ``with_ci``
    also puts `ci` in the pipeline and records it, so a rework clears a stale `ci` gate too.
    ``agent`` is who claims and merges: a second agent's later claim is the race
    (B-merged-uncompleted-claimable)."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    extra = '\n[gate.unit_tests]\ncommand = "grep -q reworked t.txt"\n'
    if with_ci:
        extra += (
            '[gates]\ntask_pipeline = ["research", "rules", "implement", "rubber_duck", "critic", '
            '"standards", "ci", "unit_tests", "bug_hunt", "dedupe", "merge"]\n'
        )
    cfg.write_text(cfg.read_text() + extra)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "cfg")
    run_cli(repo, "task", "add", "T1", "--title", "t")
    code, out, err = run_cli(repo, "--json", "claim", "T1", agent=agent)
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "t.txt").write_text("x\n")
    _git(tree, "add", ".")
    _git(tree, "commit", "-qm", "work")
    if with_ci:
        code, out, err = run_cli(
            repo, "gate", "record", "T1", "implement", "--outcome", "passed", "--evidence", "e",
            agent=agent
        )
        assert code == 0, out + err
        code, out, err = run_cli(
            repo,
            "gate",
            "skip",
            "T1",
            "ci",
            "--reason",
            "the stale ci gate is reopened by the rework",
            agent=agent,
        )
        assert code == 0, out + err
    assert run_cli(repo, "merge", "T1", agent=agent)[0] == 0
    sha = fold(EventLog(repo).read_all(), strict=False).items["T1"].merged_sha
    assert sha, "the merge records a merged_sha"
    return sha


def test_reclaiming_a_merged_item_reopens_the_stale_merge(repo):
    """The re-claim of a merged item is a rework: the recorded merge no longer stands, so
    `merged_sha` and every gate outcome clear (no stale [x] survives to completion)."""
    _merged(repo, with_ci=True)
    before = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    assert before.gate_outcome("ci") == "skipped"  # a stale ci outcome exists to be reopened
    assert before.gate_outcome("implement") == "passed"  # a stale implement pass exists too
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    it = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    assert it.merged_sha == ""
    assert it.gate_outcome("merge") == ""
    assert it.gate_outcome("ci") == ""
    assert it.gate_outcome("implement") == ""  # rework re-gates, no stale pass survives


def test_a_gate_run_after_rework_runs_in_the_new_tree(repo):
    """The symptom: with `merged_sha` cleared, `gate run` succeeds and records a real pass
    from the reworked tree instead of answering "merged, and the merge removed its worktree"
    and recording UNAVAILABLE. The command is `grep -q reworked t.txt`, which fails unless
    it runs in the reworked tree (the base has `t.txt` = "x"), so the assertion also proves
    the gate ran in the new worktree, not the primary or a stale tree."""
    _merged(repo)
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "t.txt").write_text("reworked\n")
    _git(tree, "add", ".")
    _git(tree, "commit", "-qm", "rework")
    run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed", "--evidence", "e")
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
    text = out + err
    assert code == 0, text
    assert "removed its worktree" not in text, text
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gate_outcome("unit_tests")
    assert rec == "passed", rec


def test_a_second_agents_claim_of_a_merged_uncompleted_item_is_refused(repo):
    """B-merged-uncompleted-claimable: in the merge -> complete gap another lane's claim must
    be REFUSED, not treated as fresh work. `item.reworked` would clear the merging lane's
    gates and its `complete` would then refuse. The merger's OWN re-claim still re-opens
    (test_reclaiming_a_merged_item_reopens_the_stale_merge)."""
    _merged(repo, agent="lane-1")
    before = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    assert before.merged_sha, "lane-1's merge recorded"
    assert before.gate_outcome("merge") == "passed", "the merge gate is what must survive"
    code, out, err = run_cli(repo, "--json", "claim", "T1", agent="lane-2")
    assert code == 3, out + err  # refused, not a rework
    it = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    assert it.merged_sha, "the refused claim leaves lane-1's merge intact"
    assert it.gate_outcome("merge") == "passed", "lane-1's gates are preserved"


def test_next_does_not_offer_a_merged_uncompleted_item(repo):
    """A merged item not yet completed is not fresh work: `next` for a second lane must not
    list it as ready."""
    _merged(repo, agent="lane-1")
    _code, out, _ = run_cli(repo, "--json", "next", agent="lane-2")
    plan = json.loads(out)
    ready_ids = [r["id"] for r in plan.get("ready", [])]
    assert "T1" not in ready_ids, plan
