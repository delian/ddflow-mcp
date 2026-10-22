"""`recover` offers `complete` for an item already merged to main (Ba2eff5726b).

An item whose merge gate passed and whose commit is on the base, with its lease expired and
its worktree gone, was reported as "worktree is gone; nothing to salvage. `ddflow release`".
Release only puts it back in the queue to be claimed and merged AGAIN; the one thing left to
do is finish it, and nothing said so. `scan` now names the completion, and what it still
wants.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli

from ddflow.api import lifecycle as LC
from ddflow.config import Config
from ddflow.infra.log import EventLog
from ddflow.services import leases as L


_DOC_GATES = (
    "implement",
    "research",
    "rules",
    "rubber_duck",
    "critic",
    "standards",
    "bug_hunt",
    "dedupe",
)


def _git(*a: str) -> None:
    subprocess.run(["git", *a], check=True, capture_output=True)


def _merged(
    repo: Path,
    item: str,
    rel: str,
    globs: list[str] | None = None,
    keep: bool = False,
    gates: bool = False,
) -> None:
    """``item`` claimed, one file committed on its branch, merged by ddflow."""
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", item, {"parent": "P1", "title": item, "globs": globs or [rel]})
    out = LC.claim(repo, item, agent="worker")
    assert out.exit == 0, out.reason
    wt = Path(out.data["worktree"])
    (wt / rel).parent.mkdir(parents=True, exist_ok=True)
    (wt / rel).write_text("x = 1\n")
    _git("-C", str(wt), "add", "-A")
    ident = ["-c", "user.email=t@e.com", "-c", "user.name=T"]
    _git("-C", str(wt), *ident, "commit", "-qm", f"{item} work")
    if gates:  # outcomes recorded while the work is still in its tree, as the pipeline wants
        for g in _DOC_GATES:
            run_cli(repo, "gate", "record", item, g, "--outcome", "passed", "--evidence", f"{g} ok")
    assert LC.merge(repo, item, agent="worker", keep=keep).exit == 0
    EventLog(repo, "worker").append(
        "session.started", "s1", {"agent": "worker", "model": "claude-opus-5"}
    )


def _recs(repo: Path, item: str, *, later: float = 10_000.0) -> list[L.Recovery]:
    """The scan from ``later`` seconds on: the lease has lapsed."""
    return [
        r
        for r in L.scan(EventLog(repo, "scanner"), Config.load(repo), repo, now=time.time() + later)
        if r.item == item
    ]


def test_a_merged_item_with_a_lapsed_lease_is_advised_to_complete(repo):
    _merged(repo, "T1", "src/T1/a.py")
    (rec,) = _recs(repo, "T1")
    assert rec.kind == "expired_lease"
    assert "ddflow complete T1" in rec.advice, rec.advice
    assert "merged" in rec.advice
    assert "ddflow release" not in rec.advice, rec.advice


def test_the_advice_names_what_complete_still_wants(repo):
    _merged(repo, "T1", "src/T1/a.py")
    (rec,) = _recs(repo, "T1")
    assert "required gate(s) not passed" in rec.advice and "unit_tests" in rec.advice, rec.advice


def test_the_advised_path_works(repo):
    """Another agent records the outcomes against the lapsed lease and completes."""
    _merged(repo, "T1", "src/T1/a.py")
    pass_pipeline(repo, "T1", omit=("merge",))
    assert L.expire(EventLog(repo, "scanner"), "T1", reason="lapsed"), "the lease is lapsed"
    out = LC.complete(repo, "T1", agent="rescuer", model="claude-opus-5")
    assert out.exit == 0, out.reason


def test_a_fully_recorded_item_is_told_to_complete_outright(repo):
    _merged(repo, "T1", "src/T1/a.py")
    pass_pipeline(repo, "T1", omit=("merge",))
    (rec,) = _recs(repo, "T1")
    assert "Finish it: `ddflow complete T1 --model <author model>`" in rec.advice, rec.advice


def test_a_docs_only_item_is_not_owed_the_code_gates(repo):
    """The diff, not the declared globs, decides: the item declares code AND docs, but what
    landed is a document, so it runs the doc pipeline and is not owed unit_tests."""
    _merged(repo, "T1", "docs/note.md", globs=["src/T1/*", "docs/*"])
    for g in (
        "implement",
        "research",
        "rules",
        "rubber_duck",
        "critic",
        "standards",
        "bug_hunt",
        "dedupe",
    ):
        run_cli(repo, "gate", "record", "T1", g, "--outcome", "passed", "--evidence", f"{g} ok")
    out = LC.complete(repo, "T1", agent="rescuer", model="claude-opus-5")
    assert out.exit == 0, out.reason


def test_an_unmerged_item_is_still_only_advised_to_release(repo):
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", "T2", {"parent": "P1", "title": "T2", "globs": ["src/T2/*"]})
    out = LC.claim(repo, "T2", agent="worker")
    _git("-C", str(repo), "worktree", "remove", "--force", out.data["worktree"])
    (rec,) = _recs(repo, "T2")
    assert "complete" not in rec.advice and "ddflow release T2" in rec.advice, rec.advice


def test_a_kept_clean_tree_with_no_lease_is_advised_to_complete_without_a_lease_clause(repo):
    """The orphan path, a docs-only item (no author model needed, so `complete` is open to
    it): merged with `keep`, lease released, the clean tree still there."""
    _merged(repo, "T1", "docs/note.md", globs=["docs/*"], keep=True, gates=True)
    assert L.release(EventLog(repo, "scanner"), "T1", note="released")
    (rec,) = _recs(repo, "T1")
    assert rec.kind == "orphan_worktree"
    assert "Finish it: `ddflow complete T1 --model <author model>`." in rec.advice, rec.advice
    assert "ddflow release" not in rec.advice and "lease" not in rec.advice


def test_a_finished_item_with_a_surviving_clean_tree_is_not_advised_to_complete_again(repo):
    _merged(repo, "T1", "src/T1/a.py", keep=True)
    log = EventLog(repo, "worker")
    L.release(log, "T1", note="released")
    sha = L._fold(log).items["T1"].merged_sha
    log.append("item.completed", "T1", {"sha": sha})
    assert not [r for r in _recs(repo, "T1") if "ddflow complete" in r.advice]
