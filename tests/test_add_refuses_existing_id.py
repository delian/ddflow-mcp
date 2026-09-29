"""`task add` / `phase add` refuse an id that is already in the queue.

Bugs B6bd279367e and B16042585f4 (one defect, filed twice): a second `task add <id>` was
folded as a re-definition, so it silently overwrote the existing item's title, body and
globs -- including an item RUNNING under another agent's lease. On 2026-09-28 the
companions fix filed as B207 (claimed, eight gates recorded) became "docsync test gaps"
when a second agent filed its own B207, and the first agent's lease and gate records sat
under the second agent's title, where a `complete` would have counted them.

A REMOVED item's id may be filed again, but only on purpose: `--readd` (MCP/api `readd`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.surfaces import mcp

REFUSED = 3


def _events(repo):
    return EventLog(repo, "reader").read_all()


def _item(repo, item):
    return fold(_events(repo), strict=False).items[item]


def test_a_second_task_add_is_refused_and_changes_nothing(repo):
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "task", "add", "T1", "--title", "first", "--globs", "a.py")[0] == 0
    before = len(_events(repo))
    code, out, err = run_cli(repo, "task", "add", "T1", "--title", "second", "--globs", "b.py")
    assert code == REFUSED, out + err
    assert "T1" in err and "first" in err and "task" in err, err
    it = _item(repo, "T1")
    assert (it.title, it.globs) == ("first", ["a.py"])
    assert len(_events(repo)) == before, "a refusal records nothing"


def test_a_running_item_names_its_holder_and_state(repo):
    assert run_cli(repo, "init")[0] == 0
    run_cli(repo, "task", "add", "T1", "--title", "first")
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent="alice")[0] == 0
    code, _out, err = run_cli(repo, "task", "add", "T1", "--title", "mine", agent="bob")
    assert code == REFUSED
    assert "alice" in err and "running" in err, err
    assert _item(repo, "T1").title == "first"


def test_a_phase_id_cannot_be_re_added_as_a_task_or_phase(repo):
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "phase", "add", "P1", "--title", "phase")[0] == 0
    code, _out, err = run_cli(repo, "task", "add", "P1", "--title", "task")
    assert code == REFUSED
    assert "phase" in err, err
    assert run_cli(repo, "phase", "add", "P1", "--title", "again")[0] == REFUSED
    it = _item(repo, "P1")
    assert (it.kind, it.title) == ("phase", "phase")


def test_a_removed_id_is_refused_without_readd_and_names_the_flag(repo):
    assert run_cli(repo, "init")[0] == 0
    run_cli(repo, "task", "add", "T1", "--title", "first")
    assert run_cli(repo, "remove", "T1", "--reason", "x")[0] == 0
    code, _out, err = run_cli(repo, "task", "add", "T1", "--title", "back")
    assert code == REFUSED
    assert "removed" in err and "--readd" in err, err
    it = _item(repo, "T1")
    assert (it.title, it.removed) == ("first", True)


def test_a_removed_id_may_be_filed_again_with_readd(repo):
    """Re-adding a REMOVED item is deliberate: `_h_added` un-removes it."""
    assert run_cli(repo, "init")[0] == 0
    run_cli(repo, "task", "add", "T1", "--title", "first")
    assert run_cli(repo, "remove", "T1", "--reason", "x")[0] == 0
    assert run_cli(repo, "task", "add", "T1", "--title", "back", "--readd")[0] == 0
    it = _item(repo, "T1")
    assert (it.title, it.removed) == ("back", False)
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    assert run_cli(repo, "remove", "P1", "--reason", "x")[0] == 0
    assert run_cli(repo, "phase", "add", "P1", "--title", "p2")[0] == REFUSED
    assert run_cli(repo, "phase", "add", "P1", "--title", "p2", "--readd")[0] == 0
    ph = _item(repo, "P1")
    assert (ph.title, ph.removed) == ("p2", False)


def test_readd_does_not_unlock_a_live_item(repo):
    assert run_cli(repo, "init")[0] == 0
    run_cli(repo, "task", "add", "T1", "--title", "first")
    assert run_cli(repo, "task", "add", "T1", "--title", "x", "--readd")[0] == REFUSED
    assert _item(repo, "T1").title == "first"


def test_update_is_named_as_the_way_to_change_an_item(repo):
    assert run_cli(repo, "init")[0] == 0
    run_cli(repo, "task", "add", "T1", "--title", "first")
    _code, _out, err = run_cli(repo, "task", "add", "T1", "--title", "second")
    assert "ddflow update T1" in err, err


def test_the_api_and_mcp_refuse_too(repo):
    assert run_cli(repo, "init")[0] == 0
    assert api.phase_add(repo, "P1", title="p")
    out = api.phase_add(repo, "P1", title="again")
    assert out.exit == REFUSED and "P1" in out.reason
    assert api.task_add(repo, "T1", title="t", parent="P1")
    assert api.task_add(repo, "T1", title="again").exit == REFUSED
    for tool in ("ddflow_task_add", "ddflow_phase_add"):
        assert mcp.TOOLS[tool]["properties"]["readd"][0] == "boolean", tool
    out = mcp.TOOLS["ddflow_task_add"]["api"](repo, {"id": "T1", "title": "x"}, "")
    assert out.exit == REFUSED
    assert api.remove_item(repo, "T1", reason="x")
    out = mcp.TOOLS["ddflow_task_add"]["api"](repo, {"id": "T1", "title": "x", "readd": True}, "")
    assert out.exit == 0, out.reason
    assert (_item(repo, "T1").title, _item(repo, "T1").removed) == ("x", False)
