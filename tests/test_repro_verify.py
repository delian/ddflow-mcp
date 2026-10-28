"""`bug found --repro-cmd` RUNS the command it carries (DX-bug-repro-verify).

The bundle task recorded the command so it can be re-run; the verify task RUNS it at
filing time, in a scratch checkout of the recorded sha (the regression-verify worktree
code), and the record the bug keeps says whether it reproduced. These pin that: a command
that ran to SUCCESS places `reproduced: no` (only a run that FAILS places `yes`), the run
happened at the recorded sha and NOT in the filer's dirty tree, how many runs the reading
made is a knob and the failure count is recorded, a second sighting whose run fails
overrides the earlier no, and an open no-repro bug reads `unconfirmed` in lists and is
not offered ahead of confirmed work.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import run_cli
from helpers import git, git_quiet
from helpers import state_as_reader as state

from ddflow.api import knowledge as K
from ddflow.config import Config
from ddflow.core.schedule import bug_items
from ddflow.services import reprobundle as RB


@pytest.fixture
def proj(repo: Path, monkeypatch) -> Path:
    run_cli(repo, "init")
    monkeypatch.chdir(repo)
    return repo


def _config(repo: Path, text: str) -> None:
    p = repo / ".ddflow" / "config.toml"
    p.write_text((p.read_text() if p.exists() else "") + text)


def python(source: str) -> str:
    """A repro command line that runs ``source`` with THIS interpreter (github-safe split)."""
    return f'"{sys.executable}" -c "{source}"'


def _file(repo: Path, **kw):
    kw.setdefault("summary", "it breaks")
    out = K.bug_found(repo, agent="a1", **kw)
    assert out.ok, out.reason
    return out.data["id"], state(repo).bugs[out.data["id"]]


def _run(repo: Path, bug) -> dict:
    got = RB.read(repo, bug.repro)
    assert isinstance(got, dict), got
    run = got["repro_cmd"]
    assert isinstance(run, dict), run
    return run


def test_a_run_that_fails_is_the_sighting_that_reproduces(proj):
    _bid, bug = _file(proj, repro_cmd=python("import sys; sys.exit(1)"))
    run = _run(proj, bug)
    assert run["executed"] is True
    assert run["at"] == git(proj, "rev-parse", "HEAD")
    assert run["reproduced"] == "yes" and run["runs"] == 1 and run["failures"] == 1
    assert bug.reproduced == "yes" and bug.repro_runs == 1 and bug.repro_failures == 1


def test_a_run_that_succeeded_is_never_read_as_reproduced(proj):
    _bid, bug = _file(proj, repro_cmd=python("import sys; sys.exit(0)"))
    run = _run(proj, bug)
    assert run["executed"] is True and run["reproduced"] == "no" and run["failures"] == 0
    assert bug.reproduced == "no" and bug.repro_runs == 1 and bug.repro_failures == 0


def test_how_many_runs_is_a_knob_and_partial_failures_are_the_rate(proj):
    _config(proj, "[bugs]\nrepro_runs = 3\n")
    # Fail only after the first exit: one 0, then two 1s -- the rate a flaky bug records.
    flip = python(
        "import sys; f='seen' if __import__('os').path.exists('seen') else 0; open('seen','w').write('x'); sys.exit(1 if f else 0)"
    )
    _bid, bug = _file(proj, repro_cmd=flip)
    run = _run(proj, bug)
    assert run["runs"] == 3 and run["failures"] == 2 and run["reproduced"] == "yes"
    assert bug.repro_runs == 3 and bug.repro_failures == 2 and bug.reproduced == "yes"


def test_the_run_sees_the_recorded_sha_not_the_filers_dirty_tree(proj):
    (proj / "marker.txt").write_text("committed\n")
    git_quiet(proj, "add", "marker.txt")
    git_quiet(proj, "commit", "-qm", "marker")
    (proj / "marker.txt").write_text("uncommitted\n")  # dirty: a run THERE would say so
    _bid, bug = _file(
        proj, repro_cmd=python("import sys; print(open('marker.txt').read()); sys.exit(0)")
    )
    run = _run(proj, bug)
    assert run["at"] == git(proj, "rev-parse", "HEAD")
    assert run["reproduced"] == "no"
    assert "committed" in run["output"] and "uncommitted" not in run["output"]


def test_a_second_sighting_whose_run_fails_overrides_the_earlier_no(proj):
    bid, bug = _file(proj, repro_cmd=python("import sys; sys.exit(0)"))
    assert bug.reproduced == "no"
    again = K.bug_found(
        proj, summary="it breaks", id=bid, agent="a1", repro_cmd=python("import sys; sys.exit(1)")
    )
    assert again.ok, again.reason
    bug = state(proj).bugs[bid]
    assert bug.reproduced == "yes" and bug.repro_failures == 1


def test_an_open_bug_whose_repro_did_not_fail_is_unconfirmed_in_lists(proj):
    bid, _bug = _file(proj, repro_cmd=python("import sys; sys.exit(0)"))
    code, out, _err = run_cli(proj, "list", "bug", "--all", "--json")
    assert code == 0
    rows = {r["id"]: r for r in json.loads(out)["rows"]}
    assert rows[bid]["state"] == "unconfirmed" and rows[bid]["reproduced"] == "no"


def test_an_unconfirmed_bugs_fix_task_is_not_offered_before_confirmed_work(proj):
    ok_id, ok_bug = _file(proj, repro_cmd=python("import sys; sys.exit(1)"))
    no_id, bad_bug = _file(proj, summary="quiet one", repro_cmd=python("import sys; sys.exit(0)"))
    ids = bug_items(state(proj), Config.load(proj))
    assert ok_bug.fix_task in ids  # the confirmed bug's fix is a bugs-first item
    assert bad_bug.fix_task not in ids and ok_id not in ids and no_id not in ids
    assert bad_bug.reproduced == "no"


def test_an_unconfirmed_bugs_own_item_is_not_offered_before_confirmed_work(proj):
    # The reading's leak: the bug's OWN item -- a non-feature task it was filed against,
    # with no fix task -- must be excluded too, not only its fix_task.
    run_cli(proj, "phase", "add", "P1", "--title", "Core")
    run_cli(proj, "task", "add", "P1.CARRY", "--phase", "P1", "--globs", "carry/*")
    _, carry = _file(
        proj, summary="carrier", item="P1.CARRY", no_task=True,
        repro_cmd=python("import sys; sys.exit(0)"),
    )
    assert carry.item == "P1.CARRY" and carry.reproduced == "no"
    ids = bug_items(state(proj), Config.load(proj))
    assert "P1.CARRY" in {i.id for i in state(proj).items.values()}  # the scenario really had it
    assert "P1.CARRY" not in ids and carry.fix_task == ""


def test_the_line_runs_through_the_shell_env_prefix_and_all(proj):
    # `X=val cmd` is the SHELL's syntax: run as bare argv, no run reaches an exit code and
    # the sighting is lost; through the shell the variable is set and the run answers.
    line = "X=set " + python("import os,sys; sys.exit(0 if os.environ.get('X') else 9)")
    _bid, bug = _file(proj, repro_cmd=line)
    run = _run(proj, bug)
    assert run["executed"] is True and run["reproduced"] == "no"
    assert bug.reproduced == "no"  # the 0 the passthrough env made: recorded, not lost


def test_a_second_sighting_opens_the_bug_again_even_though_its_run_passed(proj):
    bid, bug = _file(proj, repro_cmd=python("import sys; sys.exit(0)"))
    assert bug.reproduced == "no" and bug.sightings == 1
    again = K.bug_found(
        proj, summary="seen again", id=bid, agent="a1", repro_cmd=python("import sys; sys.exit(0)")
    )
    assert again.ok, again.reason
    code, out, _err = run_cli(proj, "list", "bug", "--all", "--json")
    assert code == 0
    rows = {r["id"]: r for r in json.loads(out)["rows"]}
    assert rows[bid]["state"] == "open" and rows[bid]["reproduced"] == "no"


def test_the_output_tail_is_cut_at_bytes_not_characters(proj):
    _config(proj, "[bugs]\nrepro_tail_bytes = 3\n")
    # 'é' is two UTF-8 bytes: a CHARACTER cut at 3 would store 'ééé', six bytes.
    _bid, bug = _file(proj, repro_cmd=python("import sys; sys.stdout.write('é'*5); sys.exit(0)"))
    run = _run(proj, bug)
    assert run.get("output_truncated") is True
    assert len(run["output"].encode("utf-8")) <= 3


def test_each_runs_output_is_bounded_before_the_tail_is_cut(proj):
    # The tail budget must bound MEMORY too, not only the record: each run's output is
    # pre-cut when it lands, and the joined tail still ends with the last run's bytes.
    _config(proj, "[bugs]\nrepro_runs = 3\nrepro_tail_bytes = 8\n")
    _bid, bug = _file(
        proj, repro_cmd=python("import sys; print('run-marker'); sys.exit(0)")
    )
    run = _run(proj, bug)
    assert run["output_truncated"] is True
    assert run["output"] == "-marker\n"  # the joined tail, 'run' cut by the byte budget
