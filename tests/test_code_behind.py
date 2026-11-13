"""GM-code-behind: "behind main" means behind in CODE.

Three quarters of main's first-parent commits touch only `.ddflow/` (the event log). Counting
them made every worktree "behind", so `ddflow sync`, the queue's re-merge, the commit gate's
staleness count and the session hint all acted on a movement that carried no code.
`[worktree].behind_ignore_globs` (default `.ddflow/`) names the paths that do not count; the
total is still reported, beside the number that touch code.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api import lifecycle as A
from ddflow.api import mergequeue_run as MQR
from ddflow.api.lifecycle.sync import sync
from ddflow.config import Config
from ddflow.infra import worktree as W
from ddflow.services import drift as D
from ddflow.services.enforce import driftchecks as DC

ME = "agent-code-behind"
EVENTS = ".ddflow/events/lane.jsonl"
RULES = ".ddflow/rules/r-x.md"


def _git(cwd: Path, *a: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *a],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _land(repo: Path, name: str, text: str = "x\n") -> None:
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _git(repo, "add", "-f", name)
    _git(repo, "commit", "-qm", f"main: {name}")


def _claimed(repo: Path, globs: str = "src/") -> Path:
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", globs)
    out = A.claim(repo, "T1", agent=ME)
    assert out.ok, out.reason
    from helpers import items_of

    return W.load_path(repo, items_of(repo)["T1"].worktree)


def _cfg(repo: Path) -> Config:
    return Config.load(repo)


def test_the_knob_defaults_to_the_event_log_directory(repo):
    assert _cfg(repo).worktree.behind_ignore_globs == [".ddflow/"]


def test_measure_counts_every_commit_and_those_that_touch_code_apart(repo):
    wt = _claimed(repo)
    _land(repo, EVENTS)
    _land(repo, EVENTS, "y\n")
    r = D.measure(repo, wt, cfg=_cfg(repo))
    assert (r.behind, r.code_behind) == (2, 0)
    _land(repo, "src/a.py")
    r = D.measure(repo, wt, cfg=_cfg(repo))
    assert (r.behind, r.code_behind) == (3, 1)


def test_a_commit_touching_events_and_code_is_a_code_commit(repo):
    wt = _claimed(repo)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("a\n")
    (repo / EVENTS).parent.mkdir(parents=True, exist_ok=True)
    (repo / EVENTS).write_text("e\n")
    _git(repo, "add", "-f", "src/a.py", EVENTS)
    _git(repo, "commit", "-qm", "both")
    r = D.measure(repo, wt, cfg=_cfg(repo))
    assert (r.behind, r.code_behind) == (1, 1)


def test_an_empty_ignore_list_counts_everything_as_before(repo):
    wt = _claimed(repo)
    _land(repo, EVENTS)
    cfg = _cfg(repo)
    cfg.worktree.behind_ignore_globs = []
    r = D.measure(repo, wt, cfg=cfg)
    assert (r.behind, r.code_behind) == (1, 1)


def test_the_ignore_list_is_the_users_to_widen(repo):
    wt = _claimed(repo)
    _land(repo, "docs/a.md")
    cfg = _cfg(repo)
    assert D.measure(repo, wt, cfg=cfg).code_behind == 1
    cfg.worktree.behind_ignore_globs = [".ddflow/", "docs/"]
    assert D.measure(repo, wt, cfg=cfg).code_behind == 0


def test_a_tree_that_is_not_behind_is_code_behind_zero(repo):
    wt = _claimed(repo)
    r = D.measure(repo, wt, cfg=_cfg(repo))
    assert (r.behind, r.code_behind) == (0, 0)


def test_could_not_tell_stays_none(repo):
    wt = _claimed(repo)
    r = D.measure(repo, wt, "no-such-branch", cfg=_cfg(repo))
    assert (r.behind, r.code_behind) == (None, None)


def test_since_still_counts_code_over_the_whole_range(repo):
    wt = _claimed(repo)
    _land(repo, "src/a.py")
    _land(repo, EVENTS)
    since = _git(repo, "rev-parse", "HEAD")
    _land(repo, EVENTS, "z\n")
    r = D.measure(repo, wt, since=since, cfg=_cfg(repo))
    assert (r.behind, r.code_behind) == (3, 1)


def test_sync_check_says_current_when_main_moved_by_events_only(repo):
    wt = _claimed(repo)
    _land(repo, EVENTS)
    head = _git(wt, "rev-parse", "HEAD")
    out = sync(repo, item="T1", check=True, agent=ME)
    assert out.data["status"] == "current", out.data
    assert (out.data["behind"], out.data["code_behind"]) == (1, 0)
    assert _git(wt, "rev-parse", "HEAD") == head


def test_sync_makes_no_merge_commit_when_main_moved_by_events_only(repo):
    wt = _claimed(repo)
    _land(repo, EVENTS)
    head = _git(wt, "rev-parse", "HEAD")
    out = sync(repo, item="T1", agent=ME)
    assert out.data["status"] == "current", out.data
    assert _git(wt, "rev-parse", "HEAD") == head, "a merge commit for an events-only advance"


def test_sync_reports_the_total_and_the_code_count_and_merges_when_code_moved(repo):
    wt = _claimed(repo)
    _land(repo, EVENTS)
    _land(repo, "src/a.py")
    out = sync(repo, item="T1", check=True, agent=ME)
    assert out.data["status"] == "behind"
    assert (out.data["behind"], out.data["code_behind"]) == (2, 1)
    assert "2 commit(s)" in out.data["text"] and "1 touch code" in out.data["text"], out.data
    merged = sync(repo, item="T1", agent=ME)
    assert merged.ok, merged.reason
    assert (wt / "src" / "a.py").exists()


def test_sync_still_merges_when_a_rulebook_changed_in_the_ignored_directory(repo):
    """`.ddflow/rules/` is a rulebook the commit gate blocks on: it is not just events."""
    wt = _claimed(repo)
    _land(repo, RULES)
    out = sync(repo, item="T1", agent=ME)
    assert out.data["status"] == "merged", out.data
    assert (wt / RULES).exists()


def test_the_queue_skips_the_re_merge_when_main_moved_by_events_only(repo):
    wt = _claimed(repo)
    _land(repo, EVENTS)
    head = _git(wt, "rev-parse", "HEAD")
    item = W.Worktree(item="T1", path=wt, branch=_git(wt, "branch", "--show-current"), base="main")
    assert MQR._bring_in_base(repo, _cfg(repo), item) == ""
    assert _git(wt, "rev-parse", "HEAD") == head, "the queue merged an events-only advance"


def test_the_queue_merges_when_code_moved(repo):
    wt = _claimed(repo)
    _land(repo, EVENTS)
    _land(repo, "src/a.py")
    item = W.Worktree(item="T1", path=wt, branch=_git(wt, "branch", "--show-current"), base="main")
    assert MQR._bring_in_base(repo, _cfg(repo), item) == ""
    assert (wt / "src" / "a.py").exists()


def test_the_commit_gate_counts_only_code_commits_against_max_behind(repo):
    wt = _claimed(repo)
    for i in range(5):
        _land(repo, f".ddflow/events/l{i}.jsonl")
    cfg = _cfg(repo)
    cfg.enforce.max_behind = 2
    assert DC.check_drift(repo, cfg, here=wt).message == "", (
        "events-only commits tripped max_behind"
    )
    for i in range(3):
        _land(repo, f"src/a{i}.py")
    verdict = DC.check_drift(repo, cfg, here=wt)
    assert "3 commits behind `main` in code" in verdict.message, verdict.message
    assert "8 commits in all" in verdict.message


def test_the_session_hint_is_silent_for_an_events_only_advance(repo):
    from ddflow.api.setup import session as S

    wt = _claimed(repo)
    _land(repo, EVENTS)
    assert S._worktree_drift(repo, wt) == ""
    _land(repo, "src/a.py")
    msg = S._worktree_drift(repo, wt)
    assert "2 commit(s) behind" in msg and "1 touch code" in msg, msg


@pytest.mark.parametrize("value", ['[".ddflow/", "build/"]'])
def test_the_knob_is_settable_in_the_project_file_and_by_the_cli(repo, value):
    run_cli(repo, "init")
    code, _, err = run_cli(repo, "config", "--set", "worktree.behind_ignore_globs", value)
    assert code == 0, err
    assert _cfg(repo).worktree.behind_ignore_globs == [".ddflow/", "build/"]


def test_the_knob_is_settable_by_the_environment(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_WORKTREE_BEHIND_IGNORE_GLOBS", '["a/"]')
    assert _cfg(repo).worktree.behind_ignore_globs == ["a/"]
