"""`task add --needs` and `update --needs`/`--add-needs` refuse an id that does not
exist in the queue (B4d23497469): a silent accept made the dependent wait on an id that
never comes (or hid a typo that hid a loop)."""

from __future__ import annotations

from conftest import run_cli


def _two(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "task", "add", "A", "--title", "first")[0] == 0
    assert run_cli(repo, "task", "add", "B", "--title", "second")[0] == 0


def test_task_add_refuses_an_unknown_need(repo):
    _two(repo)
    code, out, err = run_cli(repo, "task", "add", "T", "--title", "t", "--needs", "NO-SUCH-ID")
    assert code != 0
    assert "NO-SUCH-ID" in out + err


def test_task_add_names_the_nearest_real_id(repo):
    _two(repo)
    code, out, err = run_cli(repo, "task", "add", "T2", "--title", "t2", "--needs", "Aa")
    assert code != 0
    assert "Did you mean" in out + err


def test_update_add_needs_refuses_an_unknown_need(repo):
    _two(repo)
    assert run_cli(repo, "task", "add", "T", "--title", "t", "--needs", "A")[0] == 0
    code, out, err = run_cli(repo, "update", "T", "--add-needs", "GHOST")
    assert code != 0
    assert "GHOST" in out + err


def test_update_needs_refuses_an_unknown_need(repo):
    _two(repo)
    assert run_cli(repo, "task", "add", "T", "--title", "t", "--needs", "A")[0] == 0
    code, out, err = run_cli(repo, "update", "T", "--needs", "A,GHOST")
    assert code != 0
    assert "GHOST" in out + err


def test_valid_need_is_still_accepted(repo):
    _two(repo)
    assert run_cli(repo, "task", "add", "T", "--title", "t", "--needs", "A,B")[0] == 0
