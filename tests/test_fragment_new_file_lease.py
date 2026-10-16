"""Upgrade fragments are per-knob NEW files, not a shared directory (bug B92e13a521f).

Two tasks that each add a knob commit their own `unreleased/knob_added.*.toml` without
either leasing the directory; an edit of an existing fragment still needs a lease.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core import admission
from ddflow.core.model import fold
from ddflow.infra import worktree as W
from ddflow.infra.log import EventLog
from ddflow.services import enforce as E
from ddflow.services import leases as L

FRAG = "ddflow/templates/upgrade/unreleased"


def _task(repo, cfg, item, holder, glob):
    assert run_cli(repo, "task", "add", item, "--globs", glob)[0] == 0
    wt = W.create(repo, cfg, item)
    L.acquire(
        EventLog(repo, holder),
        cfg,
        item,
        holder=holder,
        worktree=W.store_path(repo, wt.path),
        branch=wt.branch,
        globs=[glob],
    )
    return wt.path


def _stage(tree: Path, rel: str, text: str) -> None:
    (tree / rel).parent.mkdir(parents=True, exist_ok=True)
    (tree / rel).write_text(text)
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True)


def test_two_tasks_each_commit_their_own_fragment(repo, cfg, monkeypatch):
    assert run_cli(repo, "init")[0] == 0
    cfg.enforce.commit_without_lease = "block"
    a = _task(repo, cfg, "TA", "ag-a", "src/a/*")
    b = _task(repo, cfg, "TB", "ag-b", "src/b/*")
    _stage(a, f"{FRAG}/knob_added.s.one.toml", "x = 1\n")
    _stage(b, f"{FRAG}/knob_added.s.two.toml", "x = 2\n")
    for tree, who in ((a, "ag-a"), (b, "ag-b")):
        monkeypatch.chdir(tree)
        code, msg = E.check_commit(repo, cfg, agent=who)
        assert code == 0, msg


def test_editing_an_existing_fragment_still_needs_a_lease(repo, cfg, monkeypatch):
    assert run_cli(repo, "init")[0] == 0
    cfg.enforce.commit_without_lease = "block"
    a = _task(repo, cfg, "TA", "ag-a", "src/a/*")
    frag = f"{FRAG}/knob_added.s.theirs.toml"
    _stage(a, frag, "x = 1\n")
    subprocess.run(["git", "-C", str(a), "commit", "-qm", "frag", "--no-verify"], check=True)
    (a / frag).write_text("x = 2\n")
    subprocess.run(["git", "-C", str(a), "add", "-A"], check=True)
    monkeypatch.chdir(a)
    code, msg = E.check_commit(repo, cfg, agent="ag-a")
    assert code == 1 and "knob_added.s.theirs.toml" in msg, msg


def test_the_knob_is_overridable(repo, cfg, monkeypatch):
    assert run_cli(repo, "init")[0] == 0
    cfg.enforce.commit_without_lease = "block"
    cfg.lease.new_file_globs = []
    a = _task(repo, cfg, "TA", "ag-a", "src/a/*")
    _stage(a, f"{FRAG}/knob_added.s.one.toml", "x = 1\n")
    monkeypatch.chdir(a)
    assert E.check_commit(repo, cfg, agent="ag-a")[0] == 1


def test_a_lease_on_an_existing_fragment_stays_exclusive(log, cfg):
    log.append("task.added", "A", {"globs": [f"{FRAG}/knob_added.s.one.toml"]})
    log.append("task.added", "B", {"globs": [f"{FRAG}/knob_added.s.one.toml"]})
    state = fold(log.read_all())
    a, b = state.items["A"], state.items["B"]
    assert admission.glob_conflict(state, cfg, a, list(a.globs), against="offered", others=[b])


def test_a_new_fragment_another_agent_holds_is_refused(repo, cfg, monkeypatch):
    assert run_cli(repo, "init")[0] == 0
    cfg.enforce.commit_without_lease = "block"
    frag = f"{FRAG}/knob_added.s.one.toml"
    a = _task(repo, cfg, "TA", "ag-a", "src/a/*")
    _task(repo, cfg, "TB", "ag-b", frag)
    _stage(a, frag, "x = 1\n")
    monkeypatch.chdir(a)
    code, msg = E.check_commit(repo, cfg, agent="ag-a")
    assert code == 1 and "held by" in msg, msg
