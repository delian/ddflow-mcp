"""`ddflow rename` and `abandon --repoint-to`: an id changes, every needs edge follows."""

from __future__ import annotations

from conftest import run_cli
from helpers import items_of as _items


def _setup(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p1")
    run_cli(repo, "phase", "add", "P2", "--title", "p2")
    run_cli(repo, "task", "add", "A", "--parent", "P1", "--title", "ta", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--parent", "P1", "--title", "tb", "--needs", "A")
    assert (
        run_cli(
            repo,
            "task",
            "add",
            "C",
            "--parent",
            "P1",
            "--title",
            "tc",
            "--needs",
            "A,B",
            "--body",
            "the body",
            "--tags",
            "x",
            "--priority",
            "7",
        )[0]
        == 0
    )


def test_rename_keeps_the_item_and_repoints_dependents(repo):
    _setup(repo)
    code, _o, err = run_cli(repo, "rename", "A", "A2")
    assert code == 0, err
    items = _items(repo)
    assert items["A2"].title == "ta" and items["A2"].globs == ["a.py"]
    assert items["A"].state == "abandoned" and "renamed to A2" in str(items["A"])
    assert items["B"].needs == ["A2"] and items["C"].needs == ["A2", "B"]


def test_rename_to_another_phase_carries_everything(repo):
    _setup(repo)
    assert run_cli(repo, "rename", "C", "C2", "--parent", "P2")[0] == 0
    c2 = _items(repo)["C2"]
    assert c2.parent == "P2" and c2.body == "the body" and c2.tags == ["x"]
    assert c2.priority == 7 and c2.needs == ["A", "B"]


def test_rename_dry_run_records_nothing(repo):
    _setup(repo)
    code, out, _e = run_cli(repo, "rename", "A", "A2", "--dry-run")
    assert code == 0 and "dry run" in out and "B" in out
    assert "A2" not in _items(repo) and _items(repo)["A"].state != "abandoned"


def test_rename_refusals(repo):
    _setup(repo)
    assert run_cli(repo, "rename", "A", "B")[0] != 0  # taken
    assert run_cli(repo, "rename", "NOPE", "X")[0] != 0
    assert run_cli(repo, "rename", "P1", "P9")[0] != 0  # a phase
    assert run_cli(repo, "rename", "A", "bad:id")[0] != 0
    assert run_cli(repo, "rename", "A", "X", "--parent", "NOPE")[0] != 0
    assert run_cli(repo, "claim", "A", "--agent", "w")[0] == 0
    code, _o, err = run_cli(repo, "rename", "A", "A2")
    assert code == 3 and "claimed" in err
    assert "A2" not in _items(repo)


def test_abandon_repoint_to_moves_the_dependents(repo):
    _setup(repo)
    code, out, err = run_cli(repo, "abandon", "A", "--reason", "dup", "--repoint-to", "B")
    assert code == 0, err
    items = _items(repo)
    assert items["A"].state == "abandoned" and items["C"].needs == ["B"]
    assert "B" in out


def test_abandon_repoint_to_refuses_a_cycle_and_a_dead_target(repo):
    _setup(repo)
    code, _o, _e = run_cli(repo, "abandon", "A", "--reason", "x", "--repoint-to", "C")
    assert code == 3  # B needs C, C needs B
    assert _items(repo)["A"].state != "abandoned"
    assert run_cli(repo, "abandon", "A", "--reason", "x", "--repoint-to", "NOPE")[0] == 1


def test_abandon_with_dependents_offers_repoint(repo):
    _setup(repo)
    _c, out, _e = run_cli(repo, "abandon", "A", "--reason", "x")
    assert "--repoint-to" in out
