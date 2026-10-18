"""`ddflow batch` and `ddflow task fold`: many edits, one all-or-nothing transaction."""

from __future__ import annotations

import json

from conftest import run_cli
from helpers import items_of as _items


def _setup(repo):
    run_cli(repo, "init")
    for t in ("A", "B", "C"):
        assert run_cli(repo, "task", "add", t, "--globs", f"{t}.py", "--body", f"body {t}")[0] == 0
    assert run_cli(repo, "task", "add", "D", "--globs", "d.py", "--needs", "B")[0] == 0


def _batch(repo, edits, *more):
    return run_cli(repo, "batch", "--edits", json.dumps(edits), *more)


def test_batch_applies_every_edit(repo):
    _setup(repo)
    code, _o, err = _batch(
        repo, [{"id": "A", "priority": 5, "add_tags": ["x"]}, {"id": "C", "append_body": "n"}]
    )
    assert code == 0, err
    items = _items(repo)
    assert items["A"].priority == 5 and items["A"].tags == ["x"]
    assert items["C"].body == "body C\n\nn"


def test_batch_dry_run_records_nothing(repo):
    _setup(repo)
    code, out, _e = _batch(repo, [{"id": "A", "priority": 5}], "--dry-run")
    assert code == 0 and "dry run" in out
    assert _items(repo)["A"].priority != 5


def test_batch_is_all_or_nothing(repo):
    _setup(repo)
    code, _o, err = _batch(repo, [{"id": "A", "priority": 5}, {"id": "ZZ", "priority": 1}])
    assert code != 0 and "ZZ" in err
    assert _items(repo)["A"].priority != 5


def test_batch_refuses_a_cycle_and_records_nothing(repo):
    _setup(repo)
    code, o, e = _batch(repo, [{"id": "B", "needs": ["D"]}, {"id": "A", "priority": 5}])
    assert code == 3 and "cycle" in o + e
    assert _items(repo)["A"].priority != 5


def test_batch_refuses_an_unknown_field_and_a_duplicate_id(repo):
    _setup(repo)
    assert _batch(repo, [{"id": "A", "bogus": 1}])[0] != 0
    assert _batch(repo, [{"id": "A", "priority": 1}, {"id": "A", "priority": 2}])[0] != 0


def test_fold_moves_body_globs_and_dependents_and_abandons(repo):
    _setup(repo)
    code, out, err = run_cli(repo, "task", "fold", "B", "A", "--reason", "dup")
    assert code == 0, err
    items = _items(repo)
    assert items["B"].state == "abandoned"
    assert items["A"].globs == ["A.py", "B.py"]
    assert "body B" in items["A"].body and items["A"].body.startswith("body A")
    assert items["D"].needs == ["A"]
    assert "D" in out


def test_fold_dry_run_changes_nothing(repo):
    _setup(repo)
    code, out, _e = run_cli(repo, "task", "fold", "B", "A", "--dry-run")
    assert code == 0 and "dry run" in out
    assert _items(repo)["B"].state != "abandoned" and _items(repo)["D"].needs == ["B"]


def test_fold_refuses_itself_and_a_claimed_duplicate(repo):
    _setup(repo)
    assert run_cli(repo, "task", "fold", "A", "A")[0] != 0
    assert run_cli(repo, "claim", "B", "--agent", "w")[0] == 0
    code, _o, err = run_cli(repo, "task", "fold", "B", "A")
    assert code == 3 and "claimed" in err


def test_apply_refuses_an_unknown_abandon_id_and_records_nothing(repo):
    from ddflow.api import batch

    _setup(repo)
    out = batch.apply(repo, [{"id": "A", "priority": 5}], abandon={"NOPE": "x"})
    assert out.exit != 0 and "NOPE" in out.reason
    assert _items(repo)["A"].priority != 5


def test_fold_json_names_the_unchanged_list(repo):
    import json as J

    _setup(repo)
    code, out, _e = run_cli(repo, "--json", "task", "fold", "B", "A", "--dry-run")
    assert code == 0 and "unchanged" in J.loads(out)


def test_batch_refuses_two_lease_retargets_and_abandoning_a_claimed_item(repo):
    from ddflow.api import batch

    _setup(repo)
    assert run_cli(repo, "claim", "A", "--agent", "w")[0] == 0
    assert run_cli(repo, "claim", "C", "--agent", "w2")[0] == 0
    code, o, e = _batch(
        repo, [{"id": "A", "add_globs": ["x1.py"]}, {"id": "C", "add_globs": ["x2.py"]}]
    )
    assert code == 3 and "more than one lease" in o + e
    out = batch.apply(repo, [], abandon={"A": "x"})
    assert out.exit == 3 and "claimed" in out.reason
