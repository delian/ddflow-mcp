"""A split task completes when its sub-tasks do (bug B651a63e574).

`split` turns a task into an umbrella and says it "completes when its children do"; the
README says the same. Nothing did it: after B-uni-fsio-writers' three sub-tasks were
merged and completed, the umbrella stayed open, `next` offered it as ready work, and
`complete` refused it for gates (implement, unit_tests, merge) that have no diff of the
umbrella's own to run on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK = 0


def _split(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P", "--title", "phase")
    run_cli(repo, "task", "add", "P.T", "--title", "big task", "--globs", "a.py")
    code, out, err = run_cli(repo, "split", "P.T", "--into", "P.T.a=one", "--into", "P.T.b=two")
    assert code == OK, out + err


def _state(repo: Path, item: str) -> str:
    return fold(EventLog(repo).read_all(), strict=False).items[item].state


def test_the_last_sub_task_completing_completes_the_umbrella(repo):
    _split(repo)
    assert run_cli(repo, "complete", "P.T.a", "--force")[0] == OK
    assert _state(repo, "P.T") != "done", "one sub-task is still open"
    code, out, err = run_cli(repo, "--json", "complete", "P.T.b", "--force")
    assert code == OK, out + err
    assert _state(repo, "P.T") == "done", "the umbrella stayed open after its last sub-task"
    assert json.loads(out)["umbrellas_completed"] == ["P.T"], out
    assert "P.T completed with its sub-tasks" in err, err
    done = [
        e for e in EventLog(repo).read_all() if e.kind == "item.completed" and e.subject == "P.T"
    ]
    assert done and done[-1].data.get("umbrella") == ["P.T.a", "P.T.b"], done


def test_a_settled_umbrella_completes_without_a_pipeline_of_its_own(repo):
    """The state B-uni-fsio-writers was left in: every child done, the umbrella open."""
    _split(repo)
    log = EventLog(repo)
    for child in ("P.T.a", "P.T.b"):
        log.append("item.completed", child, {"sha": "", "kind": "task", "forced": True})
    assert _state(repo, "P.T") == "open"

    code, out, err = run_cli(repo, "complete", "P.T")

    assert code == OK, out + err
    assert _state(repo, "P.T") == "done"


def test_an_umbrella_whose_sub_tasks_were_all_abandoned_does_not_complete(repo):
    """Nothing was done: that is not the umbrella's work finished."""
    _split(repo)
    for child in ("P.T.a", "P.T.b"):
        assert run_cli(repo, "abandon", child, "--reason", "dropped")[0] == OK
    assert _state(repo, "P.T") != "done"
    assert run_cli(repo, "complete", "P.T")[0] != OK


def test_completing_a_settled_umbrella_completes_the_one_above_it(repo):
    """Nested: P.T split into P.T.a and P.T.b, P.T.b split again into P.T.b.x and P.T.b.y. Completing the settled
    inner umbrella by hand settles the outer one too."""
    _split(repo)
    assert run_cli(repo, "split", "P.T.b", "--into", "P.T.b.x=x", "--into", "P.T.b.y=y")[0] == OK
    log = EventLog(repo)
    for child in ("P.T.a", "P.T.b.x", "P.T.b.y"):
        log.append("item.completed", child, {"sha": "", "kind": "task", "forced": True})
    assert run_cli(repo, "complete", "P.T.b")[0] == OK
    assert _state(repo, "P.T.b") == "done"
    assert _state(repo, "P.T") == "done", "the outer umbrella was left open"


def test_a_flag_on_a_settled_umbrella_is_not_dropped(repo):
    """It completes through the same path as any item: a --regression-test on an umbrella
    that fixes no bug is refused, not silently ignored (roborev on e08fb2ef)."""
    _split(repo)
    log = EventLog(repo)
    for child in ("P.T.a", "P.T.b"):
        log.append("item.completed", child, {"sha": "", "kind": "task", "forced": True})
    code, out, err = run_cli(repo, "complete", "P.T", "--regression-test", "tests/x.py")
    assert code == 3 and "not the fix task of any open bug" in out + err, out + err
    assert _state(repo, "P.T") == "open"


def test_an_umbrella_held_by_an_open_bug_says_so_instead_of_completing_silently(repo):
    """A split fix task: its bug closes with a regression test, so the last sub-task's
    completion cannot complete it -- and must say so, not report a silent success."""
    import json as _json

    _split(repo)
    code, out, err = run_cli(repo, "--json", "bug", "found", "--summary", "a bug", "--item", "P.T")
    assert code == OK, out + err
    fix = _json.loads(out)["fix_task"]
    code, out, err = run_cli(repo, "split", fix, "--into", f"{fix}.a=a", "--into", f"{fix}.b=b")
    assert code == OK, out + err
    assert run_cli(repo, "complete", f"{fix}.a", "--force")[0] == OK
    from ddflow.api.lifecycle import complete

    out = complete(repo, f"{fix}.b", force=True)
    assert out.exit == OK, out.reason
    assert fix in out.data.get("umbrella_refused", {}), out.data
    assert _state(repo, fix) == "open"
    # and the surfaces carry it: the projected body and the MCP tool's payload
    body = out.body(("id", "umbrella_refused"))
    assert fix in body["umbrella_refused"]
    from ddflow.surfaces.tools import TOOLS

    assert "umbrella_refused" in TOOLS["ddflow_complete"]["payload"]


def test_next_never_offers_a_settled_umbrella(repo):
    """B797aff72d8: an umbrella settled before the last sub-task completed it (as
    B-uni-fsio-writers was) is not ready work; next names the command that closes it."""
    _split(repo)
    log = EventLog(repo)
    for child in ("P.T.a", "P.T.b"):
        log.append("item.completed", child, {"sha": "", "kind": "task", "forced": True})
    _code, out, _err = run_cli(repo, "--json", "next")
    data = json.loads(out)
    assert "P.T" not in [r["id"] for r in data.get("ready") or []], out
    held = [b for b in data.get("blocked") or [] if b.get("item") == "P.T"]
    assert held and "ddflow complete P.T" in held[0]["detail"], data.get("blocked")
