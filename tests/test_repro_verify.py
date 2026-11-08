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
import shlex
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
    """A repro command line that runs ``source`` with THIS interpreter (github-safe split).
    The snippet is shell-QUOTED: an embedded double quote or shell-active char in it must
    not end the -c word for the shell."""
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


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


def test_how_many_runs_is_a_knob_and_each_run_sees_a_fresh_checkout(proj):
    _config(proj, "[bugs]\nrepro_runs = 3\n")
    # Each run is reset to the recorded sha first: the marker never survives into the
    # next run, so a stateful line answers like the first time, every time. Pre-reset
    # code would answer 1,0,0 -- the isolation makes it 1,1,1.
    probe = python(
        "import os,sys; open('seen','a').write('x'); sys.exit(1 if len(open('seen').read()) == 1 else 0)"
    )
    _bid, bug = _file(proj, repro_cmd=probe)
    run = _run(proj, bug)
    assert run["runs"] == 3 and run["failures"] == 3 and run["reproduced"] == "yes"
    assert bug.repro_runs == 3 and bug.repro_failures == 3 and bug.reproduced == "yes"


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
    # Both fix tasks are bugfix branches in their own right: they stay bug items. The
    # unconfirmed record counts for NOTHING: no promotion and no demotion -- what the
    # record alone would have granted (the promotion of a plain item it names) is what
    # it loses, and the fix branches stand.
    assert ok_bug.fix_task in ids and bad_bug.fix_task in ids
    assert ok_id not in ids and no_id not in ids
    assert bad_bug.reproduced == "no"


def test_a_bug_with_no_repro_evidence_is_not_demoted_like_a_failed_repro(proj):
    # A bug filed WITHOUT --repro-cmd leaves `reproduced == ""` (no run happened), not
    # "no" (a repro command RAN and did not fail). No evidence is not the demotion
    # "no" carries: such a bug is still offered, only a demonstrated non-failing repro
    # is unconfirmed. The CONTROL file first (confirmed) proves the offer is real.
    _ok_id, ok_bug = _file(proj, repro_cmd=python("import sys; sys.exit(1)"))
    quiet_id, quiet_bug = _file(proj, summary="no repro evidence")
    assert quiet_bug.reproduced == "" and quiet_bug.sightings == 1
    ids = bug_items(state(proj), Config.load(proj))
    assert ok_bug.fix_task in ids  # the confirmed control
    assert quiet_bug.fix_task in ids  # no evidence: offered, not demoted
    _code, out, _err = run_cli(proj, "list", "bug", "--all", "--json")
    rows = {r["id"]: r for r in json.loads(out)["rows"]}
    assert rows[quiet_id]["state"] == "open"  # no evidence is "open", never "unconfirmed"


def test_a_sighting_whose_run_count_advances_and_failure_count_cannot_folds_nothing(proj):
    from ddflow.core.handlers import knowledge as handlers_knowledge

    # An unparseable failures count must not advance the run count first: the fold is
    # atomic -- both counts count, or NEITHER does, so a three-run sighting is never
    # paired with a stale failure count.
    class Ev:
        def __init__(self, data):
            self.data = data
            self.ts = ""

    from ddflow.core.records import Bug

    b = Bug(id="probe")
    b.repro_failures = 5
    handlers_knowledge._merge_sighted(
        b, Ev({"sighted": {"reproduced": "yes", "runs": 1, "failures": "boom"}})
    )
    assert b.repro_runs == 0 and b.repro_failures == 5


def test_an_unconfirmed_bugs_own_item_is_not_offered_before_confirmed_work(proj):
    # The reading's leak: the bug's OWN item -- a non-feature task it was filed against,
    # with no fix task -- must be excluded too, not only its fix_task. The CONTROL: the
    # sibling plain item a CONFIRMED bug names IS promoted, so the exclusion below is
    # demonstrably the unconfirmed record's doing, not a bug_items coincidence.
    run_cli(proj, "phase", "add", "P1", "--title", "Core")
    run_cli(proj, "task", "add", "P1.CARRY", "--phase", "P1", "--globs", "carry/*")
    run_cli(proj, "task", "add", "P1.CARRY2", "--phase", "P1", "--globs", "carry2/*")
    _, carry = _file(
        proj,
        summary="carrier",
        item="P1.CARRY",
        no_task=True,
        repro_cmd=python("import sys; sys.exit(0)"),
    )
    _, promo = _file(
        proj,
        summary="promoted",
        item="P1.CARRY2",
        no_task=True,
        repro_cmd=python("import sys; sys.exit(1)"),
    )
    assert carry.item == "P1.CARRY" and carry.reproduced == "no"
    assert promo.item == "P1.CARRY2" and promo.reproduced == "yes"  # the confirmed control
    ids = bug_items(state(proj), Config.load(proj))
    assert "P1.CARRY2" in ids  # confirmed promotion works: the exclusion below is meaningful
    assert "P1.CARRY" not in ids and carry.fix_task == ""


def test_the_line_runs_through_the_shell_env_prefix_and_all(proj, monkeypatch):
    # `X=val cmd` is the SHELL's syntax: run as bare argv, no run reaches an exit code and
    # the sighting is lost; through the shell the variable is set and the run answers.
    # The variable is UNIQUE and STARTS UNSET: a stray inherited value must not decide
    # the run in place of the prefix's assignment.
    monkeypatch.delenv("DDFLOW_REPRO_PROBE_X", raising=False)
    line = "DDFLOW_REPRO_PROBE_X=set " + python(
        "import os,sys; sys.exit(0 if os.environ.get('DDFLOW_REPRO_PROBE_X') else 9)"
    )
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
    # 'é' is two UTF-8 bytes: a CHARACTER cut at 3 would store 'ééé', six bytes. The
    # stored KEEP is 3 BYTES of 'é'*5 (a9 c3 a9): the led-off fragment renders as the
    # replacement marker and the complete é renders -- the byte cut happened, drawn.
    # The BYTES are written, not text: an ascii-stdio PYTHONIOENCODING would otherwise
    # raise before any byte lands.
    _bid, bug = _file(
        proj,
        repro_cmd=python(r"import sys; sys.stdout.buffer.write(b'\xc3\xa9' * 5); sys.exit(0)"),
    )
    run = _run(proj, bug)
    assert run.get("output_truncated") is True
    assert run["output"] == "\ufffdé"  # the LAST three bytes a9 c3 a9: split fragment, then é


def test_each_runs_output_is_bounded_before_the_tail_is_cut(proj):
    # The tail budget must bound CAPTURE too, not only the record: each run's output
    # lands in a scratch FILE and only its tail is read back, and the joined tail still
    # ends with the last run's bytes.
    _config(proj, "[bugs]\nrepro_runs = 3\nrepro_tail_bytes = 8\n")
    _bid, bug = _file(proj, repro_cmd=python("import sys; print('run-marker'); sys.exit(0)"))
    run = _run(proj, bug)
    assert run["output_truncated"] is True
    assert run["output"] == "-marker\n"  # the joined tail, 'run' cut by the byte budget


def test_mcp_evidence_is_stored_verbatim_never_executed(proj):
    # `ddflow_bug_found`'s `evidence` is agent-supplied TEXT describing what showed the
    # bug; it must be STORED on the record, never EXECUTED (over MCP an agent supplies it,
    # and a lane held to its globs could otherwise run a shell through it). A value that
    # would leave a marker when run must leave none, sit verbatim on the record, and leave
    # `reproduced` empty (no repro run happened).
    from test_add_dedupe_mcp import call

    marker = proj / "evidence_ran"
    evidence = python(f"open({str(marker)!r}, 'w').write('x')")
    body, result = call(
        proj, "ddflow_bug_found", summary="mcp evidence must not run", evidence=evidence
    )
    assert not result.get("isError"), body
    bug = state(proj).bugs[body["id"]]
    assert not marker.exists()  # the server-side evidence was never executed
    assert "evidence_ran" in bug.found_evidence  # ... stored on the record instead
    assert bug.reproduced == ""  # ... with no repro run behind it


def test_the_capture_is_a_scratch_file_not_an_in_process_buffer(proj, monkeypatch):
    # Pins the mechanism: the budget must bound the run ITSELF, not cut after it. The
    # line runs in a subshell whose whole output redirects into a tail file OUTSIDE the
    # checkout, so a run printing gigabytes never reaches this process's memory and the
    # tail file never dirties the reproduced tree; a rollback to buffering the whole
    # output then slicing would keep the same tail -- and lose the redirect.
    _config(proj, "[bugs]\nrepro_tail_bytes = 8\n")
    from ddflow.infra import proc as P

    calls: list[tuple[str, dict]] = []
    real = P.run_shell
    monkeypatch.setattr(
        P, "run_shell", lambda line, **kw: calls.append((line, kw)) or real(line, **kw)
    )
    _bid, bug = _file(
        proj, repro_cmd=python("import sys; sys.stdout.write('x' * 100000); sys.exit(0)")
    )
    run = _run(proj, bug)
    line, kw = calls[0]
    assert line.lstrip().startswith("(") and " > " in line and " 2>&1" in line
    target = Path(line.split(" > ", 1)[1].split(" 2>&1", 1)[0].strip())
    target = Path(str(target).strip("'\""))
    anchored = target if target.is_absolute() else Path(kw["cwd"]) / target
    here = Path(kw["cwd"]).resolve()
    assert not anchored.resolve().is_relative_to(here)  # the tail file lives OUTSIDE the checkout
    assert run["output"] == "x" * 8
