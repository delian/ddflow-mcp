"""`update --add-needs` / `--remove-needs` change the dependency list without repeating it
(bug update-needs-replace-only)."""

from __future__ import annotations

from conftest import run_cli
from helpers import items_of as _items


def _tasks(repo):
    run_cli(repo, "init")
    for t in ("A", "B", "C"):
        assert run_cli(repo, "task", "add", t, "--globs", f"{t}.py")[0] == 0
    assert run_cli(repo, "task", "add", "T", "--globs", "t.py", "--needs", "A,B")[0] == 0


def test_add_needs_keeps_the_existing_ones(repo):
    _tasks(repo)
    code, _o, err = run_cli(repo, "update", "T", "--add-needs", "C")
    assert code == 0, err
    assert _items(repo)["T"].needs == ["A", "B", "C"]


def test_remove_needs_takes_only_those_out(repo):
    _tasks(repo)
    assert run_cli(repo, "update", "T", "--remove-needs", "A")[0] == 0
    assert _items(repo)["T"].needs == ["B"]


def test_add_and_remove_together(repo):
    _tasks(repo)
    assert run_cli(repo, "update", "T", "--add-needs", "C", "--remove-needs", "A")[0] == 0
    assert _items(repo)["T"].needs == ["B", "C"]


def test_nothing_to_change_is_exit_2(repo):
    _tasks(repo)
    code, out, err = run_cli(repo, "update", "T", "--add-needs", "A")
    assert code == 2 and "nothing to change" in out + err


def test_needs_replacing_names_what_it_dropped(repo):
    _tasks(repo)
    code, out, err = run_cli(repo, "update", "T", "--needs", "C")
    assert code == 0, err
    assert "dropped" in out and "A" in out and "B" in out


def test_add_and_remove_tags_and_tags_replace_names_the_dropped(repo):
    _tasks(repo)
    assert run_cli(repo, "update", "T", "--tags", "x,y")[0] == 0
    assert run_cli(repo, "update", "T", "--add-tags", "z", "--remove-tags", "x")[0] == 0
    assert _items(repo)["T"].tags == ["y", "z"]
    code, out, err = run_cli(repo, "update", "T", "--tags", "q")
    assert code == 0, err
    assert "dropped" in out and "y" in out and "z" in out


def test_a_noop_needs_fold_does_not_swallow_a_tags_edit(repo):
    _tasks(repo)
    assert run_cli(repo, "update", "T", "--tags", "t1")[0] == 0
    code, _o, err = run_cli(repo, "update", "T", "--remove-needs", "C", "--add-tags", "t2")
    assert code == 0, err
    assert _items(repo)["T"].tags == ["t1", "t2"]
