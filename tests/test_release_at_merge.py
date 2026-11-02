"""The lease ends when the item merges, not when it completes (LK-release-at-merge).

33.5 percent of all lease-hours fell after the merge (R-lock-landscape-verified): the item
kept files nobody could change any more while completion waited on approvals. The merge
releases the lease; the worktree and branch stay for the completion steps; `[lease]
release_at_merge = false` restores the old hold.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli
from test_merge_queue import _finish, proj  # noqa: F401


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)


def _item(repo: Path, item: str) -> dict:
    code, out, err = run_cli(repo, "--json", "show", item)
    assert code == 0, out + err
    return json.loads(out)


def _two_items(repo: Path, monkeypatch) -> Path:
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    run_cli(repo, "init")
    (repo / "c.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    for item in ("T1", "T2"):
        run_cli(repo, "task", "add", item, "--title", f"{item} fixture", "--globs", "c.txt")
    code, out, err = run_cli(repo, "--json", "claim", "T1", agent="alpha")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "c.txt").write_text("branch\n")
    _git(tree, "commit", "-qam", "branch edit")
    return tree


def test_merge_releases_the_lease_and_a_waiter_can_claim_the_file(repo, monkeypatch):
    _two_items(repo, monkeypatch)
    code, out, err = run_cli(repo, "claim", "T2", agent="beta")
    assert code != 0, "the file is held, so the waiter is refused before the merge"
    assert _item(repo, "T1")["lease"]
    code, out, err = run_cli(repo, "merge", "T1", agent="alpha")
    assert code == 0, out + err
    assert not _item(repo, "T1").get("lease"), "the merge landed but the lease is still held"
    code, out, err = run_cli(repo, "claim", "T2", agent="beta")
    assert code == 0, out + err
    # the merge only ends the lease: the item is not completed by it
    assert _item(repo, "T1")["state"] != "done"


def test_a_waiter_is_woken_ready_by_the_merge(repo, monkeypatch):
    _two_items(repo, monkeypatch)
    run_cli(repo, "claim", "T2", agent="beta")  # refused: leaves the waiting record
    assert run_cli(repo, "merge", "T1", agent="alpha")[0] == 0
    code, out, err = run_cli(repo, "wait", "--item", "T2", "--max-s", "5", agent="beta")
    assert code == 0, out + err
    assert "READY: T2" in out, out


def test_a_failed_merge_keeps_the_lease(repo, monkeypatch):
    tree = _two_items(repo, monkeypatch)
    (tree / "c.txt").write_text("dirty\n")  # uncommitted: the merge is refused
    code, _out, _err = run_cli(repo, "merge", "T1", agent="alpha")
    assert code != 0
    assert _item(repo, "T1")["lease"], "a refused merge must keep the lease"


def test_complete_works_without_a_lease_after_the_merge(repo, monkeypatch):
    _two_items(repo, monkeypatch)
    pass_pipeline(repo, "T1", omit=("merge",))
    assert run_cli(repo, "merge", "T1", agent="alpha")[0] == 0
    assert not _item(repo, "T1").get("lease")
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-test", agent="alpha")
    assert code == 0, out + err
    assert _item(repo, "T1")["state"] == "done"


def test_the_knob_off_holds_the_lease_to_complete(repo, monkeypatch):
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    run_cli(repo, "init")
    assert run_cli(repo, "config", "--set", "lease.release_at_merge", "false")[0] == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "knob")
    tree = _two_items(repo, monkeypatch)
    assert tree.exists()
    assert run_cli(repo, "merge", "T1", agent="alpha")[0] == 0
    assert _item(repo, "T1")["lease"], "release_at_merge = false keeps the old hold"
    assert run_cli(repo, "claim", "T2", agent="beta")[0] != 0, "the files stay held"


def test_a_merge_through_the_queue_releases_at_the_merge(proj):  # noqa: F811
    """The integrator lands with the ordinary merge, so the release is recorded at the merge
    (note 'merged'), not only by the integrator's own complete."""
    _finish(proj, "T1")
    assert run_cli(proj, "queue", "add", "T1", agent="t1")[0] == 0
    assert run_cli(proj, "queue", "run", agent="integrator")[0] == 0
    code, out, err = run_cli(proj, "--json", "history", "--item", "T1", "--kind", "lease.released")
    assert code == 0, out + err
    body = json.loads(out)
    events = body["events"] if isinstance(body, dict) else body
    notes = [e.get("data", e).get("note") for e in events]
    assert "merged" in notes, out
