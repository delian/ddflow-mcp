"""`task add --needs` and `update --needs`/`--add-needs` refuse an id that does not
exist in the queue (B4d23497469): a silent accept made the dependent wait on an id that
never comes (or hid a typo that hid a loop). Override only with --allow-unknown-needs."""

from __future__ import annotations

from conftest import run_cli
from helpers import items_of as _items


def _two(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "task", "add", "A", "--title", "first")[0] == 0
    assert run_cli(repo, "task", "add", "B", "--title", "second")[0] == 0


# -- task add ---------------------------------------------------------------


def test_task_add_refuses_an_unknown_need_and_writes_nothing(repo):
    _two(repo)
    code, out, err = run_cli(repo, "task", "add", "T", "--title", "t", "--needs", "NO-SUCH-ID")
    assert code != 0
    assert "NO-SUCH-ID" in out + err
    assert "T" not in _items(repo)  # nothing filed: the refusal leaves the queue unchanged


def test_task_add_names_the_nearest_real_id(repo):
    _two(repo)
    code, out, err = run_cli(repo, "task", "add", "T2", "--title", "t2", "--needs", "Aa")
    assert code != 0
    assert "Did you mean 'A'" in out + err  # the nearest real id, not just any hint
    assert "T2" not in _items(repo)  # the near-match path also files nothing


# -- update refusal (no close match) ----------------------------------------


def test_update_add_needs_refuses_an_unknown_need(repo):
    _two(repo)
    assert run_cli(repo, "task", "add", "T", "--title", "t", "--needs", "A")[0] == 0
    code, out, err = run_cli(repo, "update", "T", "--add-needs", "GHOST")
    assert code != 0
    assert "GHOST" in out + err
    assert _items(repo)["T"].needs == ["A"]  # the item's needs were not mutated


def test_update_needs_refuses_an_unknown_need(repo):
    _two(repo)
    assert run_cli(repo, "task", "add", "T", "--title", "t", "--needs", "A")[0] == 0
    code, out, err = run_cli(repo, "update", "T", "--needs", "A,GHOST")
    assert code != 0
    assert "GHOST" in out + err
    assert _items(repo)["T"].needs == ["A"]


# -- update refusal (near match) --------------------------------------------


def test_update_add_needs_names_the_nearest_real_id(repo):
    _two(repo)
    assert run_cli(repo, "task", "add", "T", "--title", "t", "--needs", "A")[0] == 0
    code, out, err = run_cli(repo, "update", "T", "--add-needs", "Ba")
    assert code != 0
    assert "Did you mean 'B'" in out + err
    assert _items(repo)["T"].needs == ["A"]  # refused, not auto-corrected to ["A", "B"]


def test_update_needs_names_the_nearest_real_id(repo):
    _two(repo)
    assert run_cli(repo, "task", "add", "T", "--title", "t", "--needs", "A")[0] == 0
    code, out, err = run_cli(repo, "update", "T", "--needs", "A,Ba")
    assert code != 0
    assert "Did you mean 'B'" in out + err
    assert _items(repo)["T"].needs == ["A"]


# -- the explicit override --------------------------------------------------


def test_allow_unknown_needs_overrides_the_refusal(repo):
    _two(repo)
    assert (
        run_cli(
            repo,
            "task",
            "add",
            "T",
            "--title",
            "t",
            "--needs",
            "NO-SUCH-ID",
            "--allow-unknown-needs",
        )[0]
        == 0
    )
    assert _items(repo)["T"].needs == ["NO-SUCH-ID"]


def test_update_allow_unknown_needs_overrides_the_refusal(repo):
    _two(repo)
    assert run_cli(repo, "task", "add", "T", "--title", "t", "--needs", "A")[0] == 0
    code, out, err = run_cli(repo, "update", "T", "--needs", "A,NOPE", "--allow-unknown-needs")
    assert code == 0, out + err
    assert _items(repo)["T"].needs == ["A", "NOPE"]


def test_update_add_needs_allow_unknown_needs_overrides(repo):
    _two(repo)
    assert run_cli(repo, "task", "add", "T", "--title", "t", "--needs", "A")[0] == 0
    code, out, err = run_cli(repo, "update", "T", "--add-needs", "NOPE", "--allow-unknown-needs")
    assert code == 0, out + err
    assert _items(repo)["T"].needs == ["A", "NOPE"]


# -- a stale need from before the fix must not block a valid edit -------------


def test_valid_need_is_still_accepted(repo):
    _two(repo)
    assert run_cli(repo, "task", "add", "T", "--title", "t", "--needs", "A,B")[0] == 0


def test_add_needs_valid_id_is_not_refused_on_a_pre_existing_unknown_need(repo):
    _two(repo)
    assert (
        run_cli(
            repo,
            "task",
            "add",
            "T",
            "--title",
            "t",
            "--needs",
            "OLD-GHOST",
            "--allow-unknown-needs",
        )[0]
        == 0
    )
    assert _items(repo)["T"].needs == ["OLD-GHOST"]  # the unknown need WAS persisted
    code, out, err = run_cli(repo, "update", "T", "--add-needs", "A")
    assert code == 0, out + err
    assert _items(repo)["T"].needs == ["OLD-GHOST", "A"]


def test_update_needs_is_not_refused_on_a_pre_existing_unknown_need(repo):
    _two(repo)
    assert (
        run_cli(
            repo,
            "task",
            "add",
            "T",
            "--title",
            "t",
            "--needs",
            "OLD-GHOST",
            "--allow-unknown-needs",
        )[0]
        == 0
    )
    assert _items(repo)["T"].needs == ["OLD-GHOST"]  # the unknown need WAS persisted
    code, out, err = run_cli(repo, "update", "T", "--needs", "A")
    assert code == 0, out + err
    assert _items(repo)["T"].needs == ["A"]
