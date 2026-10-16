"""`update --add-globs` / `--remove-globs` widen or narrow a glob list without repeating it
(DX-update-globs-help), and a space-separated `--globs a b c` is refused naming the comma form."""

from __future__ import annotations

from conftest import run_cli
from helpers import items_of as _items

from ddflow.api import lifecycle as A

AGENT = "agent-holder"


def _task(repo, globs="a.py,b.py"):
    run_cli(repo, "init")
    assert run_cli(repo, "task", "add", "T1", "--globs", globs)[0] == 0


def test_add_globs_keeps_the_existing_ones(repo):
    _task(repo)
    code, _o, err = run_cli(repo, "update", "T1", "--add-globs", "c.py,d/**")
    assert code == 0, err
    assert _items(repo)["T1"].globs == ["a.py", "b.py", "c.py", "d/**"]


def test_remove_globs_takes_only_those_out(repo):
    _task(repo)
    assert run_cli(repo, "update", "T1", "--remove-globs", "a.py")[0] == 0
    assert _items(repo)["T1"].globs == ["b.py"]


def test_add_and_remove_on_a_claimed_item_move_the_lease_too(repo):
    _task(repo)
    assert A.claim(repo, "T1", no_worktree=True, agent=AGENT).ok
    code, _o, err = run_cli(
        repo, "update", "T1", "--add-globs", "c.py", "--remove-globs", "a.py", agent=AGENT
    )
    assert code == 0, err
    it = _items(repo)["T1"]
    assert it.globs == ["b.py", "c.py"] and it.lease.globs == ["b.py", "c.py"]


def test_adding_what_is_held_is_nothing_to_change(repo):
    _task(repo)
    code, out, err = run_cli(repo, "update", "T1", "--add-globs", "a.py")
    assert code == 2 and "nothing to change" in out + err


def test_space_separated_globs_name_the_comma_form(repo):
    _task(repo)
    code, _o, err = run_cli(repo, "update", "T1", "--globs", "a.py", "b.py")
    assert code != 0 and "comma-separated" in err and "a.py,b.py" in err


def test_the_globs_help_says_replace_and_comma(repo):
    _o = run_cli(repo, "update", "--help")[1]
    flat = " ".join(_o.split())
    assert "REPLACES" in flat and "comma-separated" in flat and "--add-globs" in flat
