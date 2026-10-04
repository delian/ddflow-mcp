"""Branching models, pull requests and version tags (RESEARCH R16).

Three layers, tested at their own level:

* `core/flow.py` is pure, so its rules are tested on hand-built items with no repository.
* Local git behaviour (gitflow merges, the squash defect, tags) runs against a real repo.
* The PR loop runs end to end through the CLI, against `tests/fakeforge.py`: a fake `gh`
  whose pushes and merges land in a REAL bare remote, so "the merge is on main" is a
  statement about git. What the fake plays is the reviewer.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import pass_pipeline, run_cli
from fakeforge import STATE_ENV, Forge, install

from ddflow.config import Config
from ddflow.core import flow as F
from ddflow.core.model import REVIEW, Item, State, fold
from ddflow.infra import forge as FG
from ddflow.infra import worktree as W
from ddflow.infra.log import EventLog
from ddflow.services.flow import SyncReport, _report_queue

AUTHOR = "claude-opus-5"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _cfg(**flow) -> Config:
    c = Config()
    for k, v in flow.items():
        setattr(c.flow, k, v)
    return c


def _state(repo: Path) -> State:
    return fold(EventLog(repo, "reader").read_all(), strict=False)


# -- core: pure rules ---------------------------------------------------------------


def test_trunk_keeps_the_branch_names_ddflow_always_used():
    """A project that never writes [flow] must see no rename of anything in flight."""
    it = Item(id="P1.T1", kind="task", tags=["bug"])
    assert F.branch_name(it, _cfg()) == "ddflow/P1.T1"
    assert F.target_branch(it, _cfg(), "main") == "main"
    assert F.back_merge_targets(it, _cfg(), "main") == []


def test_gitflow_names_and_targets_follow_the_tags():
    cfg = _cfg(model="gitflow")
    feat = Item(id="T1", kind="task")
    bug = Item(id="T2", kind="task", tags=["Bug"])
    hot = Item(id="T3", kind="task", tags=["hotfix", "bug"])  # hotfix wins
    assert (F.branch_name(feat, cfg), F.target_branch(feat, cfg, "main")) == (
        "feature/T1",
        "develop",
    )
    assert (F.branch_name(bug, cfg), F.target_branch(bug, cfg, "main")) == ("bugfix/T2", "develop")
    assert (F.branch_name(hot, cfg), F.target_branch(hot, cfg, "main")) == ("hotfix/T3", "main")
    assert F.back_merge_targets(hot, cfg, "main") == ["develop"]
    assert F.back_merge_targets(bug, cfg, "main") == []


def test_an_unknown_enum_value_is_a_problem_not_a_silent_default():
    """`model = "git-flow"` would otherwise read as "not gitflow" and quietly run trunk."""
    assert F.problems(_cfg()) == []
    bad = F.problems(_cfg(model="git-flow", integration="pull", initial_version="1.0"))
    assert len(bad) == 3, bad
    assert any("git-flow" in b for b in bad)


@pytest.mark.parametrize(
    ("msg", "want"),
    [
        ("feat: add x", "minor"),
        ("feat(api)!: drop y", "major"),
        ("fix: z\n\nBREAKING CHANGE: the flag is gone", "major"),
        ("fix(core): off by one", "patch"),
        ("perf: faster", "patch"),
        ("chore: tidy", ""),
        ("Merge branch 'x'", ""),
        ("feature: not a conventional type", ""),
    ],
)
def test_conventional_commit_bumps(msg, want):
    assert F.commit_bump(msg) == want


def test_semver_bump_below_one_does_not_declare_a_stable_api():
    assert F.bump((0, 4, 2), "major") == (0, 5, 0)
    assert F.bump((1, 4, 2), "major") == (2, 0, 0)
    assert F.bump((1, 4, 2), "minor") == (1, 5, 0)
    assert F.bump((1, 4, 2), "patch") == (1, 4, 3)
    assert F.strongest(["patch", "minor", ""]) == "minor"


def test_stacking_on_two_unmerged_branches_is_refused_not_guessed():
    """Picking one would leave the other's code out of the dependent's tree, and its
    tests would pass against a world that will not exist once both land."""
    st = State()
    for i in ("A", "B"):
        st.items[i] = Item(id=i, kind="task", state=REVIEW, branch=f"ddflow/{i}")
    st.items["C"] = Item(id="C", kind="task", needs=["A", "B"])
    cfg = _cfg()
    assert F.stack_base(st, st.items["C"], cfg, ["A"]).base == "ddflow/A"
    both = F.stack_base(st, st.items["C"], cfg, ["A", "B"])
    assert both.base == "" and "2 unmerged branches" in both.error
    assert F.stack_base(st, st.items["C"], _cfg(stack=False), ["A"]).base == ""


def test_the_scheduler_parks_review_and_stacks_or_waits_by_the_knob():
    from ddflow.core.schedule import plan

    st = State()
    st.items["A"] = Item(id="A", kind="task", state=REVIEW, branch="ddflow/A")
    st.items["C"] = Item(id="C", kind="task", needs=["A"])
    p = plan(st, _cfg())
    assert [i.id for i in p.review] == ["A"], "an item in review must not be offered"
    assert [i.id for i in p.ready] == ["C"], "stacking should let the dependent start"
    p = plan(st, _cfg(stack=False))
    assert [i.id for i in p.ready] == []
    assert "awaiting review" in p.blocked[0].detail


# -- forge adapters: output mapping ---------------------------------------------------


@pytest.mark.parametrize(
    ("rollup", "want"),
    [
        ([], ""),
        ([{"status": "COMPLETED", "conclusion": "SUCCESS"}], "passing"),
        ([{"status": "IN_PROGRESS", "conclusion": ""}], "pending"),
        ([{"status": "COMPLETED", "conclusion": "SUCCESS"}, {"state": "FAILURE"}], "failing"),
        # A legacy commit status from an external CI: `state`, no `status`.
        ([{"state": "PENDING"}], "pending"),
        ([{"state": "SUCCESS"}, {"status": "COMPLETED", "conclusion": "SKIPPED"}], "passing"),
    ],
)
def test_github_check_rollup(rollup, want):
    assert FG._gh_checks(rollup) == want


def test_gitlab_approved_needs_a_named_approver():
    """`approved: true` on a project with no approval rules means nobody HAD to
    approve, not that somebody did. Merging on it would merge unreviewed work."""
    gl = FG.GitLab(Path("."))
    mr = {"iid": 7, "state": "opened", "target_branch": "main", "source_branch": "x", "sha": "abc"}
    calls = iter([mr, {"approved": True, "approved_by": []}])
    gl._api = lambda *a, what: next(calls)  # type: ignore[method-assign]
    assert gl.view(7).review == "pending"
    calls = iter([mr, {"approved": True, "approved_by": [{"user": {"username": "bob"}}]}])
    assert gl.view(7).review == "approved"
    merged = {
        **mr,
        "state": "merged",
        "merge_commit_sha": "m1",
        "head_pipeline": {"status": "success"},
    }
    info = gl._info(merged, approved=True)
    assert (info.state, info.merge_sha, info.checks) == ("merged", "m1", "passing")


def test_unreachable_forge_is_unavailable_not_refused(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(FG.ForgeUnavailable):
        FG.GitHub(Path(".")).view(1)


# -- local git: gitflow, squash, tags --------------------------------------------------


def _commit(tree: Path, name: str, text: str, msg: str) -> None:
    (tree / name).write_text(text)
    _git(tree, "add", name)
    _git(tree, "commit", "-qm", msg)


def test_squash_strategy_actually_commits(repo):
    """`git merge --squash` stages and commits NOTHING (`-m` is ignored). ddflow used to
    report the squash merge as done with the work sitting uncommitted in the primary."""
    cfg = Config()
    cfg.worktree.merge_strategy = "squash"
    before = _git(repo, "rev-parse", "HEAD")
    wt = W.create(repo, cfg, "T1")
    _commit(wt.path, "f.py", "x\n", "feat: f")
    r = W.merge(repo, cfg, wt, message="squash T1")
    assert r.ok, r.err
    assert _git(repo, "rev-parse", "HEAD") != before, "the squash was never committed"
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == "", "left staged"
    assert _git(repo, "log", "-1", "--format=%s") == "squash T1"


@pytest.fixture
def gitflow(repo):
    """Primary checkout on develop, main behind it: the usual gitflow working state."""
    _git(repo, "branch", "develop")
    _git(repo, "checkout", "-q", "develop")
    run_cli(repo, "init")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[flow]\nmodel = "gitflow"\n')
    return repo


def test_gitflow_hotfix_lands_on_main_AND_develop_without_a_checkout(gitflow):
    """The primary is on develop and stays there. main is merged in a throwaway tree;
    this used to be refused outright ("check out main yourself")."""
    repo = gitflow
    run_cli(repo, "task", "add", "H1", "--tags", "hotfix")
    code, out, err = run_cli(repo, "--json", "claim", "H1")
    assert code == 0, err
    claimed = json.loads(out)
    assert claimed["branch"] == "hotfix/H1"
    assert claimed["base"] == "main"
    tree = Path(claimed["worktree"])
    _commit(tree, "fix.py", "fixed\n", "fix: the outage")
    code, out, err = run_cli(repo, "merge", "H1")
    assert code == 0, err
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "develop", "primary was switched"
    for branch in ("main", "develop"):
        assert _git(repo, "show", f"{branch}:fix.py") == "fixed", f"hotfix missing on {branch}"
    assert "back-merged into develop" in err


def test_merge_into_a_branch_checked_out_elsewhere_is_refused(repo):
    cfg = Config()
    _git(repo, "branch", "other")
    wt_other = repo.parent / "other-tree"
    _git(repo, "worktree", "add", str(wt_other), "other")
    wt = W.create(repo, cfg, "T1")
    _commit(wt.path, "f.py", "x\n", "feat: f")
    r = W.merge_into(repo, cfg, "other", wt.branch, message="m")
    assert r.code == W.GIT_REFUSED and "checked out in the worktree" in r.err
    assert not _git(wt_other, "status", "--porcelain")


def test_trunk_version_cut_tags_and_counts_from_the_tag(repo):
    run_cli(repo, "init")
    _git(repo, "add", ".ddflow")
    _git(repo, "commit", "-qm", "chore: adopt ddflow")
    _commit(repo, "a.py", "a\n", "feat: a")
    code, out, err = run_cli(repo, "--json", "version", "show")
    assert code == 0, err
    shown = json.loads(out)
    assert (shown["current"], shown["next"], shown["bump"]) == ("", "0.1.0", "minor")

    code, out, err = run_cli(repo, "--json", "version", "cut")
    assert code == 0, err
    assert json.loads(out)["tag"] == "v0.1.0"
    assert _git(repo, "cat-file", "-t", "v0.1.0") == "tag", "must be an ANNOTATED tag"
    assert [r.version for r in _state(repo).releases] == ["0.1.0"]

    code, _out, _err = run_cli(repo, "version", "cut")
    assert code == 2, "nothing since the tag must be exit 2, not a second tag"

    _commit(repo, "b.py", "b\n", "fix: b")
    code, out, _err = run_cli(repo, "--json", "version", "show")
    assert json.loads(out)["next"] == "0.1.1"
    code, out, _err = run_cli(repo, "--json", "version", "cut", "--version", "0.0.9")
    assert code == 3 and "not above" in json.loads(out)["warning"] + _err


def test_gitflow_version_cut_back_merges_the_tag_into_develop(gitflow):
    """The tag lands on main's merge commit. Unless THAT commit reaches develop, the
    next version on develop is computed from the previous tag, recounting this release."""
    repo = gitflow
    _git(repo, "add", ".ddflow")
    _git(repo, "commit", "-qm", "chore: adopt")
    _commit(repo, "a.py", "a\n", "feat: a")
    code, out, err = run_cli(repo, "--json", "version", "cut")
    assert code == 0, err
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "develop"
    assert _git(repo, "show", "main:a.py") == "a"
    _git(repo, "merge-base", "--is-ancestor", "v0.1.0", "develop")  # raises if not
    assert "release/0.1.0" not in _git(repo, "branch", "--list", "release/*")
    code, out, _err = run_cli(repo, "--json", "version", "show")
    assert code == 2, f"develop must be AT v0.1.0 now: {out}"


# -- the PR loop, end to end against the fake forge --------------------------------------


@pytest.fixture
def pr_repo(repo, tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    run_cli(repo, "init")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[flow]\nintegration = "pr"\nforge = "github"\n')
    _git(repo, "add", ".ddflow")
    _git(repo, "commit", "-qm", "chore: adopt ddflow")
    _git(repo, "push", "-q", "origin", "main")
    bindir, state = install(tmp_path, remote)
    monkeypatch.setenv("PATH", f"{bindir}:{Path('/usr/bin')}:{Path('/bin')}")
    monkeypatch.setenv(STATE_ENV, str(state))
    return repo, Forge(state), remote


def _work(repo: Path, item: str, name: str, text: str, *, needs: str = "") -> Path:
    args = ["task", "add", item, "--globs", name]
    if needs:
        args += ["--needs", needs]
    if item not in _state(repo).items:
        run_cli(repo, *args)
    code, out, err = run_cli(repo, "--json", "claim", item)
    assert code == 0, err
    tree = Path(json.loads(out)["worktree"])
    _commit(tree, name, text, f"feat: {item}")
    pass_pipeline(repo, item, omit=("merge",))
    return tree


def test_merge_in_pr_mode_opens_a_request_and_frees_the_agent(pr_repo):
    repo, forge, _remote = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    code, out, err = run_cli(repo, "--json", "merge", "T1", "--model", AUTHOR)
    assert code == 0, err
    body = json.loads(out)
    assert body["pr"] == "https://github.com/o/r/pull/1" and body["base"] == "main"
    it = _state(repo).items["T1"]
    assert it.state == REVIEW and it.lease is None, "the agent must be free to move on"
    assert it.pr.author_model == AUTHOR
    assert _git(repo, "rev-parse", "main") == _git(repo, "rev-parse", "origin/main"), (
        "nothing may land on main without the forge"
    )
    assert "Item: T1" in forge.pr(1)["body"]

    code, out, _err = run_cli(repo, "--json", "next")
    assert json.loads(out)["review"] == ["T1"]
    code, out, _err = run_cli(repo, "recover")
    assert "T1" not in out, "a tree parked for review is not a crash to salvage"


def test_an_approved_green_request_is_merged_and_the_item_completes(pr_repo):
    repo, forge, remote = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    code, out, err = run_cli(repo, "--json", "pr", "sync")
    assert code == 0, err
    assert json.loads(out)["waiting"][0]["review"] == "pending"
    assert not forge.calls("pr", "merge"), "merged without an approval"

    forge.approve(1)
    forge.edit(1, checks=[{"status": "COMPLETED", "conclusion": "SUCCESS"}])
    code, out, err = run_cli(repo, "--json", "pr", "sync")
    assert code == 0, err
    whats = [c["what"] for c in json.loads(out)["changes"]]
    assert whats[:3] == ["updated", "merged_by_ddflow", "merged"], whats
    assert "completed" in whats
    it = _state(repo).items["T1"]
    assert it.state == "done"
    assert it.gates["merge"].evidence["pr"].endswith("/pull/1")
    merge_call = forge.calls("pr", "merge")[0]
    assert "--match-head-commit" in merge_call, "must pin the approved head"
    assert (
        subprocess.run(
            ["git", "--git-dir", str(remote), "cat-file", "-e", "main:a.py"], check=False
        ).returncode
        == 0
    )
    assert not Path(it.worktree or "/nonexistent").exists()


def test_failing_checks_hold_an_approved_request(pr_repo):
    repo, forge, _ = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    forge.approve(1)
    forge.edit(1, checks=[{"status": "COMPLETED", "conclusion": "FAILURE"}])
    run_cli(repo, "pr", "sync")
    assert not forge.calls("pr", "merge")
    assert _state(repo).items["T1"].state == REVIEW


def test_human_merge_mode_never_merges(pr_repo):
    repo, forge, _ = pr_repo
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('pr_merge = "human"\n')
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    forge.approve(1)
    run_cli(repo, "pr", "sync")
    assert not forge.calls("pr", "merge")
    forge.merge_as_human(1)
    run_cli(repo, "pr", "sync")
    assert _state(repo).items["T1"].state == "done", "a person's merge must still complete it"


def test_requested_changes_come_back_as_work_with_the_review_attached(pr_repo):
    repo, forge, _ = pr_repo
    tree = _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    forge.request_changes(
        1, "rename it", [{"path": "a.py", "line": 1, "user": {"login": "bob"}, "body": "typo"}]
    )
    code, out, err = run_cli(repo, "--json", "next")
    assert code == 0, err
    body = json.loads(out)
    assert any("changes_requested" in c for c in body["synced"]["changes"]), body
    assert [i["id"] for i in body["ready"]] == ["T1"]
    it = _state(repo).items["T1"]
    assert it.state == "open" and it.pr.rounds == 1
    assert "rename it" in it.pr.feedback and "a.py:1 bob: typo" in it.pr.feedback

    code, out, _err = run_cli(repo, "--json", "brief", "--item", "T1")
    assert json.loads(out)["brief"].startswith("## Review feedback on T1")

    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, err
    assert Path(json.loads(out)["worktree"]) == tree, "must resume the same tree"
    _commit(tree, "a.py", "renamed\n", "fix: review")
    code, out, err = run_cli(repo, "--json", "merge", "T1", "--model", AUTHOR)
    assert code == 0, err
    assert len(forge.st["prs"]) == 1, "a re-push must update the request, not open another"
    it = _state(repo).items["T1"]
    assert it.state == REVIEW and it.pr.feedback == ""


def test_a_closed_request_is_parked_for_a_person(pr_repo):
    repo, forge, _ = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    forge.edit(1, state="CLOSED")
    run_cli(repo, "pr", "sync")
    it = _state(repo).items["T1"]
    assert it.state == "blocked" and "closed without merging" in it.blocked_reason


def test_an_incomplete_pipeline_opens_no_request(pr_repo):
    """A request spends a person's time. One for work ddflow would refuse to complete
    even once merged spends it on nothing -- and the refusal would come after the merge."""
    repo, forge, _ = pr_repo
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, out, _ = run_cli(repo, "--json", "claim", "T1")
    _commit(Path(json.loads(out)["worktree"]), "a.py", "a\n", "feat: a")
    code, _out, err = run_cli(repo, "merge", "T1", "--model", AUTHOR)
    assert code == 3 and "could not complete even once merged" in err
    assert forge.st["prs"] == [] and not forge.calls("pr", "create")
    assert _state(repo).items["T1"].state != REVIEW


def test_an_unreachable_forge_is_exit_2_and_changes_nothing(pr_repo):
    repo, forge, _ = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    forge.set(offline=True)
    code, _out, err = run_cli(repo, "merge", "T1", "--model", AUTHOR)
    assert code == 2, err
    assert _state(repo).items["T1"].state == "running"
    forge.set(offline=False)
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    forge.set(offline=True)
    code, _out, err = run_cli(repo, "pr", "sync")
    assert code == 2, "could-not-ask must never read as nothing-changed"


def test_a_dependent_stacks_on_a_request_in_review_and_is_retargeted(pr_repo):
    repo, forge, _ = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    run_cli(repo, "task", "add", "T2", "--globs", "b.py", "--needs", "T1")
    code, out, err = run_cli(repo, "--json", "claim", "T2")
    assert code == 0, err
    assert json.loads(out)["base"] == "ddflow/T1", "T2 must fork from T1's branch"
    tree = Path(json.loads(out)["worktree"])
    assert (tree / "a.py").exists(), "the stacked tree lacks its dependency's code"
    _commit(tree, "b.py", "b\n", "feat: b")
    pass_pipeline(repo, "T2", omit=("merge",))
    code, out, err = run_cli(repo, "--json", "merge", "T2", "--model", AUTHOR)
    assert code == 0, err
    assert forge.pr(2)["base"] == "ddflow/T1"
    assert "Stacked on T1" in forge.pr(2)["body"]

    forge.approve(2)  # approving the stacked one first must NOT merge it into T1's branch
    run_cli(repo, "pr", "sync")
    assert not forge.calls("pr", "merge")

    forge.merge_as_human(1)
    code, out, err = run_cli(repo, "--json", "pr", "sync")
    assert code == 0, err
    assert forge.pr(2)["base"] == "main", "the stacked request was not retargeted"
    assert _state(repo).items["T2"].pr.base == "main"
    run_cli(repo, "pr", "sync")
    assert _state(repo).items["T2"].state == "done"


def test_pr_status_reads_the_log_without_asking_the_forge(pr_repo):
    repo, forge, _ = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    before = len(forge.st["calls"])
    code, out, err = run_cli(repo, "--json", "pr", "status")
    assert code == 0, err
    assert json.loads(out)["rows"][0]["id"] == "T1"
    assert len(forge.st["calls"]) == before


# -- the adversarial review of the first version (2026-09-27) ---------------------------


def test_an_item_in_review_cannot_be_claimed_without_force(pr_repo):
    """Claiming it let a push ride into a request its reviewers had already judged."""
    repo, _forge, _ = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    code, _out, err = run_cli(repo, "claim", "T1")
    assert code == 3 and "in review" in err
    assert _state(repo).items["T1"].state == REVIEW


def test_an_approval_of_an_older_head_does_not_merge_a_newer_push(pr_repo):
    """GitHub does not dismiss stale approvals by default. Merging on one pinned
    `--match-head-commit` to the head ddflow saw -- not the head that was approved."""
    repo, forge, _remote = pr_repo
    tree = _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    forge.approve(1)
    _commit(tree, "evil.py", "x\n", "feat: unreviewed")
    _git(tree, "push", "-q", "origin", "ddflow/T1")
    run_cli(repo, "pr", "sync")
    assert not forge.calls("pr", "merge"), "merged a head nobody approved"
    assert _state(repo).items["T1"].state == REVIEW
    forge.approve(1)  # the reviewer looks again, at the new head
    run_cli(repo, "pr", "sync")
    assert forge.calls("pr", "merge")
    assert _state(repo).items["T1"].state == "done"


def test_an_answered_change_request_does_not_bounce_the_item_again(pr_repo):
    """The forge keeps saying CHANGES_REQUESTED until the reviewer re-reviews, so after
    the fix was pushed every sync sent the item back with the same, answered, feedback."""
    repo, forge, _ = pr_repo
    tree = _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    forge.request_changes(1, "rename it")
    run_cli(repo, "pr", "sync")
    run_cli(repo, "claim", "T1")
    _commit(tree, "a.py", "renamed\n", "fix: review")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    for _ in range(2):
        run_cli(repo, "pr", "sync")
    it = _state(repo).items["T1"]
    assert it.state == REVIEW and it.pr.rounds == 1, (it.state, it.pr.rounds)
    forge.request_changes(1, "still wrong")  # a NEW request, on the new head
    run_cli(repo, "pr", "sync")
    it = _state(repo).items["T1"]
    assert it.state == "open" and it.pr.rounds == 2


def test_decisions_are_seen_without_a_required_review_rule(pr_repo):
    """`reviewDecision` is null when no rule requires review; the reviews still exist."""
    repo, forge, _ = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    forge.request_changes(1, "no", decision=False)
    run_cli(repo, "pr", "sync")
    assert _state(repo).items["T1"].state == "open"


def test_a_stacked_request_merged_into_its_dependency_is_not_done_until_it_lands(pr_repo):
    repo, forge, _remote = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    run_cli(repo, "task", "add", "T2", "--globs", "b.py", "--needs", "T1")
    _work(repo, "T2", "b.py", "b\n")
    run_cli(repo, "merge", "T2", "--model", AUTHOR)
    forge.merge_as_human(2)  # into ddflow/T1, by a person
    run_cli(repo, "pr", "sync")
    assert _state(repo).items["T2"].state == REVIEW, "done while main lacks its code"
    forge.edit(1, state="CLOSED")
    run_cli(repo, "pr", "sync")
    t2 = _state(repo).items["T2"]
    assert t2.state == "blocked" and "not on main" in t2.blocked_reason, t2.blocked_reason


def test_a_stacked_request_merged_into_its_dependency_lands_with_it(pr_repo):
    repo, forge, _ = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    run_cli(repo, "task", "add", "T2", "--globs", "b.py", "--needs", "T1")
    _work(repo, "T2", "b.py", "b\n")
    run_cli(repo, "merge", "T2", "--model", AUTHOR)
    forge.merge_as_human(2)
    forge.merge_as_human(1)
    run_cli(repo, "pr", "sync")
    st = _state(repo)
    assert (st.items["T1"].state, st.items["T2"].state) == ("done", "done")


def test_abandoning_an_item_with_an_open_request_is_refused(pr_repo):
    repo, _forge, _ = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    code, _out, err = run_cli(repo, "abandon", "T1", "--reason", "x")
    assert code == 3 and "open request" in err, err
    code, _out, _err = run_cli(repo, "abandon", "T1", "--reason", "x", "--force")
    assert code == 0


def test_an_unreachable_forge_is_asked_once_not_once_per_request(pr_repo):
    repo, forge, _ = pr_repo
    for t, f in (("T1", "a.py"), ("T2", "b.py")):
        _work(repo, t, f, f"{t}\n")
        run_cli(repo, "merge", t, "--model", AUTHOR)
    forge.set(offline=True, calls=[])
    code, out, _ = run_cli(repo, "--json", "pr", "sync")
    assert code == 2
    assert len(forge.st["calls"]) == 1, forge.st["calls"]
    assert "not asked: T2" in json.loads(out)["unavailable"][0]


@pytest.fixture
def gitflow_pr(pr_repo):
    repo, forge, remote = pr_repo
    _git(repo, "branch", "develop")
    _git(repo, "push", "-q", "origin", "develop")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('model = "gitflow"\n')
    return repo, forge, remote


def test_a_closed_release_request_frees_the_version(gitflow_pr):
    repo, forge, _ = gitflow_pr
    _git(repo, "checkout", "-q", "develop")
    _commit(repo, "a.py", "a\n", "feat: a")
    _git(repo, "push", "-q", "origin", "develop")
    code, _out, err = run_cli(repo, "--json", "version", "cut")
    assert code == 0, err
    assert _state(repo).pending_releases, "release request not recorded"
    forge.edit(1, state="CLOSED")
    run_cli(repo, "pr", "sync")
    assert not _state(repo).pending_releases, "a closed release is re-polled forever"


def test_a_merged_release_request_is_tagged_and_sent_back_from_production(gitflow_pr):
    repo, forge, remote = gitflow_pr
    _git(repo, "checkout", "-q", "develop")
    _commit(repo, "a.py", "a\n", "feat: a")
    _git(repo, "push", "-q", "origin", "develop")
    run_cli(repo, "version", "cut")
    forge.merge_as_human(1)
    code, _out, err = run_cli(repo, "--json", "pr", "sync")
    assert code == 0, err
    assert _git(repo, "cat-file", "-t", "v0.1.0") == "tag"
    assert (
        subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "v0.1.0"], capture_output=True
        ).returncode
        == 0
    ), "the tag was not pushed"
    back = forge.pr(2)
    assert (back["head"], back["base"]) == ("main", "develop"), back


def test_abandoning_a_done_item_refuses_instead_of_crashing(repo):
    """Pre-existing, found while adding the REVIEW refusal beside it: the refusal passed
    `reason=` as a wire field to `O.refused`, whose own parameter is `reason`, so the
    command died with a TypeError (exit 1) and the refusal was never shown."""
    from conftest import finish

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1")
    code, _out, err = finish(repo, "T1")
    assert code == 0, err
    code, _out, err = run_cli(repo, "abandon", "T1", "--reason", "x")
    assert code == 3, err
    assert "already done" in err and "Traceback" not in err


def test_a_promotion_in_pr_mode_is_a_request_into_the_environment(pr_repo):
    """GitLab flow's deploy approval: the promotion's request, into the environment
    branch, approved by a person -- and merged by ddflow only once it is."""
    repo, forge, remote = pr_repo
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('environments = ["production"]\n')
    _git(repo, "branch", "production")
    _git(repo, "push", "-q", "origin", "production")
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    forge.merge_as_human(1)
    run_cli(repo, "pr", "sync")
    code, out, err = run_cli(repo, "--json", "promote", "add", "production")
    assert code == 0, err
    pid = json.loads(out)["id"]
    code, out, err = run_cli(repo, "--json", "claim", pid)
    assert code == 0, err
    assert json.loads(out)["base"] == "origin/production"
    assert json.loads(out)["port"]["status"] == "clean", json.loads(out)["port"]
    pass_pipeline(repo, pid)
    code, out, err = run_cli(repo, "--json", "merge", pid)
    assert code == 0, err
    assert forge.pr(2)["base"] == "production"
    forge.approve(2)
    run_cli(repo, "pr", "sync")
    assert _state(repo).items[pid].state == "done"
    assert (
        subprocess.run(
            ["git", "--git-dir", str(remote), "cat-file", "-e", "production:a.py"]
        ).returncode
        == 0
    ), "the environment branch on the forge must have the work"


def test_queue_ejection_not_reported_when_queue_unknown():
    """When the merge queue couldn't be read due to transient failure, do not report ejection.

    Bug B79402e967e: _report_queue incorrectly reports queue_ejected when:
    - PR was previously queued (was_queued=True)
    - PR is still open (state="open")
    - Queue info couldn't be read (queue_known=False)
    - Queue appears empty (queue="")

    This is a transient failure (rate limit, network) not an actual ejection.
    """
    # PR was previously queued
    it = Item(id="T1", kind="task", pr=FG.PRInfo(number=1, state="open", queue="QUEUED"))
    it.pr.queue_position = 1

    # Queue info couldn't be read (transient failure)
    info = FG.PRInfo(
        number=1, state="open", queue="", queue_known=False, url="http://example.com/pr/1"
    )

    # Report queue status - should NOT report ejection
    rep = SyncReport()
    _report_queue(it, info, moved=False, was_queued=True, rep=rep)

    # Bug: this incorrectly reports queue_ejected when queue_known=False
    assert not any(c.what == "queue_ejected" for c in rep.changes), (
        "Should not report ejection when queue info couldn't be read (queue_known=False)"
    )


def test_pr_event_data_carries_queue_known():
    """B79402e967e: the synced event must record that the queue could not be read.

    Without queue_known in the event the projection defaults it to True, so the next
    sync reads the empty queue as an ejection.
    """
    unread = FG.PRInfo(number=1, state="open", queue="", queue_known=False)
    assert unread.event_data("github")["queue_known"] is False
    assert FG.PRInfo(number=1, state="open").event_data("github")["queue_known"] is True
