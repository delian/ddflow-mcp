"""Release lines, ports between them, and workflow choices (RESEARCH R17).

Real repositories throughout: a port is a statement about what ends up on which branch,
and the only honest check of that is `git show <branch>:<file>`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import pass_pipeline, run_cli

from ddflow.config import Config
from ddflow.core import flow as F
from ddflow.core.model import Item, State, fold
from ddflow.infra.log import EventLog
from ddflow.services import choices as CH

AUTHOR = "claude-opus-5"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _state(repo: Path) -> State:
    return fold(EventLog(repo, "reader").read_all(), strict=False)


def _cfg(**flow) -> Config:
    c = Config()
    for k, v in flow.items():
        setattr(c.flow, k, v)
    return c


LINES = {"1": "maint/1.x", "2": "maint/2.x"}


# -- pure rules ----------------------------------------------------------------------


def test_forward_merge_writes_on_the_oldest_and_passes_through_every_line():
    plan = F.plan_ports(_cfg(lines=LINES, current_line="3"), ["3", "1"], F.FORWARD_MERGE)
    assert plan.author == "1"
    assert plan.ports == [("2", -1), ("3", 0)], "each line merges from the one before it"
    assert "2" in plan.note, "an intermediate line added to the chain must be said"


def test_cherry_pick_writes_on_the_newest_and_ports_independently():
    plan = F.plan_ports(_cfg(lines=LINES, current_line="3"), ["1", "3", "2"], F.CHERRY_PICK)
    assert plan.author == "3"
    assert plan.ports == [("2", -1), ("1", -1)], "every port carries the fix itself"
    assert plan.note == ""


def test_a_line_is_inherited_and_a_maintenance_line_targets_its_branch():
    st = State()
    st.items["P"] = Item(id="P", kind="phase", line="2")
    st.items["T"] = Item(id="T", kind="task", parent="P")
    st.items["U"] = Item(id="U", kind="task", parent="P", line="1")
    cfg = _cfg(lines=LINES, current_line="3", model="gitflow")
    assert F.effective_line(st, st.items["T"]) == "2"
    assert F.effective_line(st, st.items["U"]) == "1", "the item's own line wins"
    assert F.target_branch(st.items["T"], cfg, "main", "2") == "maint/2.x"
    # The current line still follows the model.
    assert F.target_branch(Item(id="X", kind="task"), cfg, "main", "") == "develop"
    hot = Item(id="H", kind="task", tags=["hotfix"])
    assert F.back_merge_targets(hot, cfg, "main", "2") == [], "no develop on a maint line"


def test_line_configuration_problems_are_named():
    bad = F.problems(_cfg(lines={"3": "maint/3", "1": ""}, current_line="3", port_strategy="x"))
    assert len(bad) == 3, bad


# -- choices -------------------------------------------------------------------------


def test_config_beats_a_recorded_choice_which_beats_the_default(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "--json", "flow", "choose", "integration", "pr", "--reason", "x")
    assert code == 0 and json.loads(out)["in_effect"] is True, err
    code, out, _ = run_cli(repo, "--json", "flow", "show")
    row = next(r for r in json.loads(out)["choices"] if r["knob"] == "integration")
    assert (row["value"], row["source"]) == ("pr", "log:explicit")

    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[flow]\nintegration = "merge"\n')
    code, out, _ = run_cli(repo, "--json", "flow", "show")
    row = next(r for r in json.loads(out)["choices"] if r["knob"] == "integration")
    assert (row["value"], row["source"]) == ("merge", "file")
    assert "overridden" in row, "a recorded choice not in effect must say so"
    code, out, _ = run_cli(repo, "--json", "flow", "choose", "integration", "pr")
    assert json.loads(out)["in_effect"] is False


def test_an_invalid_choice_is_refused(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "flow", "choose", "port_strategy", "rebase")
    assert code == 1 and "forward-merge" in err
    code, _out, err = run_cli(repo, "flow", "choose", "colour", "blue")
    assert code == 1 and "not a workflow choice" in err


def test_an_undecided_choice_is_asked_then_defaulted_on_the_record_and_followed(repo):
    """Asked in the brief; defaulted at first use; FOLLOWED afterwards -- a recorded
    default is not re-decided by whatever default a later ddflow ships."""
    run_cli(repo, "init")
    _code, out, _ = run_cli(repo, "--json", "brief")
    assert "Open workflow choices" in json.loads(out)["brief"]
    assert "**model**" in json.loads(out)["brief"]
    run_cli(repo, "task", "add", "T1")
    run_cli(repo, "claim", "T1")
    rec = _state(repo).flow_choices
    assert rec["model"]["by"] == "default" and rec["model"]["value"] == "trunk"
    _code, out, _ = run_cli(repo, "--json", "brief")
    assert "**model**" not in json.loads(out)["brief"], "a defaulted choice is decided"

    # A later ddflow with a different default: the project keeps what it recorded.
    cfg = Config.load(repo)
    cfg.flow.model = "gitflow"  # stands in for a changed dataclass default
    CH.overlay(cfg, _state(repo))
    assert cfg.flow.model == "trunk"


# -- lines end to end ------------------------------------------------------------------


@pytest.fixture
def lined(repo):
    """main is line 3; maint/1.x and maint/2.x are older lines, forked from main."""
    (repo / "lib.py").write_text("def f():\n    return 1\n")
    _git(repo, "add", "lib.py")
    _git(repo, "commit", "-qm", "lib")
    for b in LINES.values():
        _git(repo, "branch", b)
    run_cli(repo, "init")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write(
            '\n[flow]\ncurrent_line = "3"\n[flow.lines]\n"1" = "maint/1.x"\n"2" = "maint/2.x"\n'
        )
    _git(repo, "add", ".ddflow")
    _git(repo, "commit", "-qm", "adopt")
    return repo


def _land(repo: Path, item: str, *, edit: tuple[str, str] | None = None) -> dict:
    code, out, err = run_cli(repo, "--json", "claim", item)
    assert code == 0, err
    claimed = json.loads(out)
    tree = Path(claimed["worktree"])
    if edit:
        (tree / edit[0]).write_text(edit[1])
        _git(tree, "add", edit[0])
        _git(tree, "commit", "-qm", f"fix: {item}")
    pass_pipeline(repo, item)
    code, _out, err = run_cli(repo, "merge", item)
    assert code == 0, err
    code, _out, err = run_cli(repo, "complete", item, "--model", AUTHOR)
    assert code == 0, err
    return claimed


def test_forward_merge_carries_a_fix_up_through_every_line(lined):
    repo = lined
    code, out, err = run_cli(
        repo, "--json", "task", "add", "FIX", "--lines", "1,3", "--globs", "lib.py"
    )
    assert code == 0, err
    added = json.loads(out)
    assert (added["line"], added["ports"]) == ("1", ["FIX@2", "FIX@3"])
    assert added["defaulted"] == ["port_strategy"], "an unmade choice must be recorded"

    code, out, _ = run_cli(repo, "--json", "next")
    assert [i["id"] for i in json.loads(out)["ready"]] == ["FIX"], "ports wait for the fix"
    claimed = _land(repo, "FIX", edit=("lib.py", "def f():\n    return 2\n"))
    assert claimed["base"] == "maint/1.x"
    assert "return 2" in _git(repo, "show", "maint/1.x:lib.py")
    assert "return 1" in _git(repo, "show", "maint/2.x:lib.py"), "nothing may leak early"

    for port, branch in (("FIX@2", "maint/2.x"), ("FIX@3", "main")):
        code, out, err = run_cli(repo, "--json", "claim", port)
        assert code == 0, err
        body = json.loads(out)
        assert body["port"]["status"] == "clean", body["port"]
        pass_pipeline(repo, port)
        assert run_cli(repo, "merge", port)[0] == 0
        assert run_cli(repo, "complete", port, "--model", AUTHOR)[0] == 0
        assert "return 2" in _git(repo, "show", f"{branch}:lib.py"), f"{port} did not land"


def test_cherry_pick_ports_run_in_parallel_and_carry_exactly_what_landed(lined):
    repo = lined
    run_cli(repo, "flow", "choose", "port_strategy", "cherry-pick", "--reason", "diverged")
    code, out, err = run_cli(
        repo, "--json", "task", "add", "FIX", "--lines", "1,2,3", "--globs", "lib.py"
    )
    assert code == 0, err
    added = json.loads(out)
    assert (added["line"], added["ports"], added["defaulted"]) == ("3", ["FIX@2", "FIX@1"], [])
    _land(repo, "FIX", edit=("lib.py", "def f():\n    return 2\n"))
    code, out, _ = run_cli(repo, "--json", "next")
    assert sorted(i["id"] for i in json.loads(out)["ready"]) == ["FIX@1", "FIX@2"]
    code, out, err = run_cli(repo, "--agent", "a", "--json", "claim", "FIX@1")
    assert code == 0, err
    code, _out2, err = run_cli(repo, "--agent", "b", "--json", "claim", "FIX@2")
    assert code == 0, f"same globs, different lines: no conflict. {err}"
    tree = Path(json.loads(out)["worktree"])
    assert json.loads(out)["port"]["status"] == "clean"
    assert "return 2" in (tree / "lib.py").read_text()
    assert "Ported-from: FIX" in _git(tree, "log", "-1", "--format=%B")


def test_a_conflicting_port_comes_back_as_work_not_as_a_failure(lined):
    repo = lined
    run_cli(repo, "flow", "choose", "port_strategy", "cherry-pick")
    # 1.x has diverged on the very line the fix changes.
    tmp = repo.parent / "m1"
    _git(repo, "worktree", "add", "-q", str(tmp), "maint/1.x")
    (tmp / "lib.py").write_text("def f():\n    return 100\n")
    _git(tmp, "commit", "-qam", "1.x only")
    _git(repo, "worktree", "remove", str(tmp))

    run_cli(repo, "task", "add", "FIX", "--lines", "1,3", "--globs", "lib.py")
    _land(repo, "FIX", edit=("lib.py", "def f():\n    return 2\n"))
    code, out, err = run_cli(repo, "--json", "claim", "FIX@1")
    assert code == 0, err
    body = json.loads(out)
    assert body["port"]["status"] == "conflict" and body["port"]["files"] == ["lib.py"]
    assert "resolve the markers" in body["port_advice"]
    assert "<<<<<<<" in (Path(body["worktree"]) / "lib.py").read_text()


def test_same_line_same_globs_still_conflict(lined):
    repo = lined
    run_cli(repo, "task", "add", "A", "--line", "2", "--globs", "lib.py")
    run_cli(repo, "task", "add", "B", "--line", "2", "--globs", "lib.py")
    assert run_cli(repo, "--agent", "a", "claim", "A")[0] == 0
    assert run_cli(repo, "--agent", "b", "claim", "B")[0] == 3


def test_a_phase_puts_its_tasks_on_its_line(lined):
    repo = lined
    run_cli(repo, "phase", "add", "P", "--line", "2")
    run_cli(repo, "task", "add", "T", "--phase", "P")
    code, out, err = run_cli(repo, "--json", "claim", "T")
    assert code == 0, err
    assert json.loads(out)["base"] == "maint/2.x"


def test_an_unknown_line_is_refused_not_read_as_current(lined):
    code, _out, err = run_cli(lined, "task", "add", "T", "--line", "7")
    assert code == 1 and "unknown release line" in err
    assert "T" not in _state(lined).items
    code, _out, err = run_cli(lined, "update", "T", "--line", "7")
    assert code == 1


def test_a_maintenance_line_keeps_its_major(lined):
    repo = lined
    _git(repo, "tag", "-a", "v1.0.0", "-m", "1.0.0", "maint/1.x")
    tmp = repo.parent / "m1"
    _git(repo, "worktree", "add", "-q", str(tmp), "maint/1.x")
    (tmp / "a.py").write_text("x\n")
    _git(tmp, "add", "a.py")
    _git(tmp, "commit", "-qm", "fix: a")
    code, out, err = run_cli(repo, "--json", "version", "show", "--line", "1")
    assert code == 0, err
    assert (json.loads(out)["current"], json.loads(out)["next"]) == ("1.0.0", "1.0.1")
    (tmp / "b.py").write_text("y\n")
    _git(tmp, "add", "b.py")
    _git(tmp, "commit", "-qm", "feat!: breaks")
    _git(repo, "worktree", "remove", str(tmp))
    code, _out, err = run_cli(repo, "version", "cut", "--line", "1")
    assert code == 3 and "maintenance line" in err
    code, out, err = run_cli(repo, "--json", "version", "cut", "--line", "1", "--bump", "minor")
    assert code == 0, err
    assert json.loads(out)["tag"] == "v1.1.0"
    _git(repo, "merge-base", "--is-ancestor", "v1.1.0", "maint/1.x")


def test_single_line_projects_see_no_change(repo):
    """No [flow.lines]: `--line` has nothing to name, and nothing else moves."""
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "task", "add", "T", "--line", "2")
    assert code == 1 and "no release lines" in err
    run_cli(repo, "task", "add", "T")
    code, out, _ = run_cli(repo, "--json", "claim", "T")
    assert json.loads(out)["branch"] == "ddflow/T"
    assert "port_strategy" not in _state(repo).flow_choices


def test_a_port_waits_for_its_source_to_LAND_not_merely_to_reach_review():
    """Stacking lets a dependent start on an unmerged branch; a port cannot -- it applies
    what landed, and in review nothing has."""
    from ddflow.core.model import REVIEW
    from ddflow.core.schedule import plan

    st = State()
    st.items["FIX"] = Item(id="FIX", kind="task", state=REVIEW, branch="ddflow/FIX")
    st.items["FIX@1"] = Item(
        id="FIX@1", kind="task", needs=["FIX"], port_from="FIX", port_of="FIX", line="1"
    )
    st.items["DEP"] = Item(id="DEP", kind="task", needs=["FIX"])
    p = plan(st, _cfg(lines=LINES, current_line="3"))
    assert [i.id for i in p.ready] == ["DEP"], "an ordinary dependent may stack"
    assert any(b.item == "FIX@1" and "land" in b.detail for b in p.blocked)


def test_next_offers_the_same_file_on_another_line_while_one_is_held(lined):
    repo = lined
    run_cli(repo, "task", "add", "A", "--line", "1", "--globs", "lib.py")
    run_cli(repo, "task", "add", "B", "--line", "2", "--globs", "lib.py")
    run_cli(repo, "task", "add", "C", "--line", "1", "--globs", "lib.py")
    assert run_cli(repo, "--agent", "a", "claim", "A")[0] == 0
    _code, out, _ = run_cli(repo, "--agent", "b", "--json", "next")
    body = json.loads(out)
    assert [i["id"] for i in body["ready"]] == ["B"], body
    assert any(b["item"] == "C" for b in body["blocked"]), "same line, same file: held"


def test_gitflow_tags_a_maintenance_line_where_it_stands(lined):
    repo = lined
    _git(repo, "branch", "develop")
    cfg = (
        (repo / ".ddflow" / "config.toml")
        .read_text()
        .replace('[flow]\ncurrent_line = "3"', '[flow]\nmodel = "gitflow"\ncurrent_line = "3"')
    )
    (repo / ".ddflow" / "config.toml").write_text(cfg)
    _git(repo, "tag", "-a", "v1.0.0", "-m", "1.0.0", "maint/1.x")
    tmp = repo.parent / "m1"
    _git(repo, "worktree", "add", "-q", str(tmp), "maint/1.x")
    (tmp / "a.py").write_text("x\n")
    _git(tmp, "add", "a.py")
    _git(tmp, "commit", "-qm", "fix: a")
    _git(repo, "worktree", "remove", str(tmp))
    code, out, err = run_cli(repo, "--json", "version", "cut", "--line", "1")
    assert code == 0, err
    assert json.loads(out)["tag"] == "v1.0.1"
    assert _git(repo, "rev-parse", "v1.0.1^{commit}") == _git(repo, "rev-parse", "maint/1.x")
    assert not _git(repo, "branch", "--list", "release/*"), "no release branch for a maint line"


# -- the adversarial review of 708d0d6 ---------------------------------------------------


def test_no_line_and_the_current_line_by_name_are_one_branch(lined):
    """`""` and `"3"` both land on main; comparing the strings let two agents onto one
    file -- and every port to the current line carries the name."""
    repo = lined
    run_cli(repo, "task", "add", "A", "--globs", "lib.py")
    run_cli(repo, "task", "add", "B", "--line", "3", "--globs", "lib.py")
    assert run_cli(repo, "--agent", "a", "claim", "A")[0] == 0
    code, _out, err = run_cli(repo, "--agent", "b", "claim", "B")
    assert code == 3, f"two agents on lib.py on main: {err}"


def test_the_line_of_a_planned_port_or_a_forked_item_cannot_move(lined):
    """Moving a fix re-aimed its forward-merge ports: a whole newer major was merged into
    an old line. Moving a forked item merged its old base's history into another line."""
    repo = lined
    run_cli(repo, "task", "add", "FIX", "--lines", "1,2")
    for item in ("FIX", "FIX@2"):
        code, _out, err = run_cli(repo, "update", item, "--line", "3")
        assert code == 3 and "planned port" in err, err
    run_cli(repo, "task", "add", "T", "--line", "1")
    run_cli(repo, "claim", "T")
    code, _out, err = run_cli(repo, "update", "T", "--line", "2")
    assert code == 3 and "forked" in err, err
    run_cli(repo, "task", "add", "U", "--line", "1")
    assert run_cli(repo, "update", "U", "--line", "2")[0] == 0, "unstarted work may move"


def test_merge_refuses_a_branch_forked_from_another_lines_base(lined):
    repo = lined
    _git(repo, "branch", "maint/2.y", "main")
    run_cli(repo, "task", "add", "T", "--line", "2")
    _code, out, _ = run_cli(repo, "--json", "claim", "T")
    tree = Path(json.loads(out)["worktree"])
    (tree / "t.py").write_text("t\n")
    _git(tree, "add", "t.py")
    _git(tree, "commit", "-qm", "t")
    cfgp = repo / ".ddflow" / "config.toml"
    cfgp.write_text(cfgp.read_text().replace('"2" = "maint/2.x"', '"2" = "maint/2.y"'))
    code, _out, err = run_cli(repo, "merge", "T")
    assert code == 3 and "forked from" in err, err


def test_a_forced_early_port_is_not_recorded_and_is_applied_on_the_next_claim(lined):
    repo = lined
    run_cli(repo, "task", "add", "FIX", "--lines", "1,2", "--globs", "lib.py")
    code, out, err = run_cli(repo, "--json", "claim", "FIX@2", "--force")
    assert code == 0, err
    assert json.loads(out)["port"]["status"] == "not_ready"
    assert not _state(repo).items["FIX@2"].port, "an unapplied port must not be recorded"
    run_cli(repo, "release", "FIX@2")
    _land(repo, "FIX", edit=("lib.py", "def f():\n    return 2\n"))
    code, out, err = run_cli(repo, "--json", "claim", "FIX@2")
    assert code == 0, err
    body = json.loads(out)
    assert body["port"]["status"] == "clean", body
    assert "return 2" in (Path(body["worktree"]) / "lib.py").read_text()


def test_a_line_removed_from_config_does_not_fall_back_to_current(lined):
    repo = lined
    run_cli(repo, "task", "add", "T", "--line", "2")
    cfgp = repo / ".ddflow" / "config.toml"
    cfgp.write_text(cfgp.read_text().replace('"2" = "maint/2.x"\n', ""))
    code, _out, err = run_cli(repo, "claim", "T")
    assert code == 3 and "not in [flow.lines]" in err, err


def test_a_port_in_a_tree_ddflow_did_not_make_is_explained_not_silent(lined):
    repo = lined
    run_cli(repo, "task", "add", "FIX", "--lines", "1,2", "--globs", "lib.py")
    _land(repo, "FIX", edit=("lib.py", "def f():\n    return 2\n"))
    code, out, err = run_cli(repo, "--json", "claim", "FIX@2", "--no-worktree")
    assert code == 0, err
    body = json.loads(out)
    assert body["port"]["status"] == "manual" and "git merge" in body["port_advice"]


def test_a_fix_on_two_lines_is_in_both_lines_release_notes(lined):
    """Excluding an item once ANY release listed it dropped a forward-merged fix from the
    other line's notes and bump."""
    repo = lined
    _git(repo, "tag", "-a", "v1.0.0", "-m", "1", "maint/1.x")
    _git(repo, "tag", "-a", "v3.0.0", "-m", "3", "main")
    run_cli(repo, "task", "add", "FIX", "--lines", "1,3", "--globs", "lib.py", "--tags", "bug")
    _land(repo, "FIX", edit=("lib.py", "def f():\n    return 2\n"))
    for port in ("FIX@2", "FIX@3"):
        _land(repo, port)
    code, out, err = run_cli(repo, "--json", "version", "cut")
    assert code == 0 and "FIX" in json.loads(out)["notes"], err
    code, out, err = run_cli(repo, "--json", "version", "show", "--line", "1")
    assert code == 0, err
    assert "FIX" in json.loads(out)["items"], json.loads(out)


def test_completing_without_a_sha_keeps_the_sha_merge_recorded(repo):
    """Pre-existing: `complete` without --sha wrote "sha": "" and the fold let it erase
    the merged sha, so no finished item was ever found on a branch."""
    log = EventLog(repo, "a")
    log.append("task.added", "T", {})
    log.append("worktree.merged", "T", {"sha": "abc123", "branch": "b"})
    log.append("item.completed", "T", {"sha": "", "kind": "task"})
    assert _state(repo).items["T"].merged_sha == "abc123"


def test_port_of_files_a_followup_fix_for_the_same_lines(lined):
    """B180: a fix amended after its ports were generated is a NEW item; `--port-of FIX`
    gives it the lines FIX reached, so its own ports carry what IT lands."""
    repo = lined
    run_cli(repo, "flow", "choose", "port_strategy", "cherry-pick")
    run_cli(repo, "task", "add", "FIX", "--lines", "1,2,3", "--globs", "lib.py")
    _land(repo, "FIX", edit=("lib.py", "def f():\n    return 2\n"))

    code, out, err = run_cli(
        repo, "--json", "task", "add", "FIX2", "--port-of", "FIX", "--globs", "extra.py"
    )
    assert code == 0, err
    added = json.loads(out)
    assert (added["line"], sorted(added["ports"])) == ("3", ["FIX2@1", "FIX2@2"]), added
    _land(repo, "FIX2", edit=("extra.py", "AMENDED = True\n"))
    code, out, err = run_cli(repo, "--json", "claim", "FIX2@1")
    assert code == 0, err
    body = json.loads(out)
    assert body["port"]["status"] == "clean", body["port"]
    assert "AMENDED" in (Path(body["worktree"]) / "extra.py").read_text()

    # Naming a PORT resolves to the fix it came from: the same lines again.
    code, out, err = run_cli(repo, "--json", "task", "add", "FIX3", "--port-of", "FIX@2")
    assert code == 0, err
    assert sorted(json.loads(out)["ports"]) == ["FIX3@1", "FIX3@2"]


def test_port_of_refuses_an_unknown_item_and_a_clash_with_lines(lined):
    repo = lined
    code, _, err = run_cli(repo, "task", "add", "X", "--port-of", "NOPE")
    assert code != 0 and "NOPE" in err, err
    run_cli(repo, "task", "add", "FIX", "--lines", "1,3", "--globs", "lib.py")
    code, _, err = run_cli(repo, "task", "add", "Y", "--port-of", "FIX", "--lines", "2")
    assert code != 0 and "--lines" in err, err
