"""`bug invalid` ends the bug's own fix task when the caller holds its lease (B8f9668d27d).

It used to say "stays in the queue: somebody holds it" about the caller's own claim, so a
separate `abandon` was needed. A task held by ANOTHER agent is still left alone, and the
reply names the holder.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state
from test_bugs_as_items import seed

from ddflow.api import knowledge as K
from ddflow.core.model import ABANDONED


def _filed_and_claimed(repo, claimer: str):
    seed(repo)
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    code, _, err = run_cli(repo, "claim", "fix-Bx", "--no-worktree", agent=claimer)
    assert code == 0, err


def test_bug_invalid_abandons_the_fix_task_the_caller_holds(repo):
    _filed_and_claimed(repo, "a")
    out = K.bug_invalid(repo, "Bx", reason="by design", agent="a")
    assert out.exit == 0, out.reason
    assert out.data["fix_task_abandoned"] == "fix-Bx"
    assert out.data["fix_task_kept"] == "" and out.data["fix_task_removed"] == ""
    task = state(repo).items["fix-Bx"]
    assert task.state == ABANDONED and not task.lease
    reason = next(e for e in _events(repo, "fix-Bx", "item.abandoned"))
    assert "by design" in reason.data["reason"] and "Bx" in reason.data["reason"]


def test_bug_invalid_from_the_cli_says_the_task_was_ended(repo):
    _filed_and_claimed(repo, "a")
    code, out, err = run_cli(repo, "bug", "invalid", "Bx", "--reason", "by design", agent="a")
    assert code == 0, err
    assert "fix-Bx ended (abandoned) and released from your lease" in out


def test_bug_invalid_leaves_another_agents_fix_task_and_names_the_holder(repo):
    _filed_and_claimed(repo, "fixer")
    code, out, err = run_cli(repo, "bug", "invalid", "Bx", "--reason", "by design", agent="a")
    assert code == 0, err
    assert "held by fixer" in out and "not touched" in out
    task = state(repo).items["fix-Bx"]
    assert task.state != ABANDONED and task.lease and task.lease.holder.startswith("fixer")


def test_bug_invalid_without_a_fix_task_still_closes(repo):
    """Review of B8f9668d27d: the new reply fields exist when there is no fix task."""
    seed(repo)
    K.bug_found(repo, summary="x", item="T1", id="Bn", agent="a", no_task=True)
    out = K.bug_invalid(repo, "Bn", reason="by design", agent="a")
    assert out.exit == 0, out.reason
    assert out.data["fix_task_abandoned"] == "" and out.data["fix_task_holder"] == ""


def test_bug_invalid_reports_the_worktree_it_leaves(repo):
    seed(repo)
    K.bug_found(repo, summary="x", item="T1", id="Bw", agent="a")
    code, _, err = run_cli(repo, "claim", "fix-Bw", agent="a")
    assert code == 0, err
    out = K.bug_invalid(repo, "Bw", reason="by design", agent="a")
    assert out.data["fix_task_abandoned"] == "fix-Bw"
    assert "kept" in out.data["fix_task_tree"] and "ddflow cleanup" in out.data["fix_task_tree"]


def test_bug_invalid_ends_the_callers_blocked_fix_task_too(repo):
    """Review of B8f9668d27d: `block` keeps the lease, so a blocked own task is still held."""
    _filed_and_claimed(repo, "a")
    code, _, err = run_cli(repo, "block", "fix-Bx", "--reason", "waiting", agent="a")
    assert code == 0, err
    out = K.bug_invalid(repo, "Bx", reason="by design", agent="a")
    assert out.data["fix_task_abandoned"] == "fix-Bx", out.data
    assert state(repo).items["fix-Bx"].state == ABANDONED


def _events(repo, item, kind):
    from ddflow.infra.log import EventLog

    return [e for e in EventLog(repo).read_all() if e.kind == kind and e.subject == item]
